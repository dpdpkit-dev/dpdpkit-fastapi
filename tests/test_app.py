from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from dpdpkit import Contact, FileRootSink, MemoryNotifier
from dpdpkit.fastapi import DPDPKit, require_consent
from dpdpkit.guardians import CallbackVerifier, GuardianStatus
from dpdpkit.models import RootHash, ScheduleStatus

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "fastapi_shop"
CONFIG = EXAMPLE / "dpdpkit.yaml"


class Clock:
    def __init__(self) -> None:
        self.now = dt.datetime(2027, 6, 1, tzinfo=dt.timezone.utc)

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def setup(clock: Clock) -> tuple[TestClient, DPDPKit]:
    app = FastAPI()
    dpdp = DPDPKit(
        db_url="sqlite://",
        config=CONFIG,
        principal_resolver=lambda r: r.headers.get("X-User"),
        admin_guard=lambda r: r.headers.get("X-Role"),
        notifier=MemoryNotifier(),
        contact_resolver=lambda p: Contact(email=f"{p}@example.in"),
        signing_key="k",
        migrate_on_startup=True,
        clock=clock,
    )
    dpdp.install(app)

    @app.get("/offers", dependencies=[Depends(require_consent("marketing"))])
    def offers() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/ping")
    def ping(request: Request) -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app), dpdp


def test_require_consent_dependency(setup: tuple[TestClient, DPDPKit]) -> None:
    client, _ = setup
    assert client.get("/offers").status_code == 401
    denied = client.get("/offers", headers={"X-User": "u1"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "consent_required"
    assert denied.json()["error"]["details"]["purpose"] == "marketing"
    client.post(
        "/dpdp/consents", json={"decisions": [{"purpose": "marketing", "granted": True}]}, headers={"X-User": "u1"}
    )
    assert client.get("/offers", headers={"X-User": "u1"}).status_code == 200


def test_roles(setup: tuple[TestClient, DPDPKit]) -> None:
    client, _ = setup
    assert client.get("/dpdp/admin/requests").status_code == 401
    assert client.get("/dpdp/admin/requests", headers={"X-User": "u1"}).json()["error"]["code"] == "forbidden"
    assert client.get("/dpdp/admin/requests", headers={"X-Role": "viewer"}).status_code == 200
    assert client.get("/dpdp/admin/audit/export", headers={"X-Role": "handler"}).status_code == 403
    assert client.get("/dpdp/admin/audit/export", headers={"X-Role": "admin"}).status_code == 200
    assert client.get("/dpdp/admin/requests", headers={"X-Role": "superuser"}).status_code == 401


def test_incident_register_and_report_draft(setup: tuple[TestClient, DPDPKit], clock: Clock) -> None:
    client, dpdp = setup
    handler = {"X-Role": "handler"}
    aware = clock.now - dt.timedelta(hours=2)
    body = {"title": "Leaked export", "aware_at": aware.isoformat(), "affected_count": 5, "severity": "high"}
    assert client.post("/dpdp/admin/incidents", json=body, headers={"X-Role": "viewer"}).status_code == 403
    created = client.post("/dpdp/admin/incidents", json=body, headers=handler)
    assert created.status_code == 201, created.text
    inc = created.json()
    assert inc["status"] == "open" and inc["severity"] == "high"
    assert dt.datetime.fromisoformat(inc["board_report_due"]) == aware + dt.timedelta(hours=72)
    assert [c["kind"] for c in inc["clocks"]][:2] == ["board_initial", "board_detailed"]

    listed = client.get("/dpdp/admin/incidents?open=true", headers={"X-Role": "viewer"}).json()["items"]
    assert [i["id"] for i in listed] == [inc["id"]]
    assert client.get(f"/dpdp/admin/incidents/{inc['id']}", headers={"X-Role": "viewer"}).json()["id"] == inc["id"]

    report = client.get(f"/dpdp/admin/incidents/{inc['id']}/report?kind=cert_in", headers=handler)
    assert report.status_code == 200 and report.headers["content-type"].startswith("text/markdown")
    assert "DRAFT" in report.text and "[TO COMPLETE" in report.text
    assert report.headers["x-dpdp-missing"]
    assert client.get("/dpdp/admin/incidents/nope/report", headers=handler).json()["error"]["code"] == "not_found"
    assert client.post("/dpdp/admin/incidents", json={"title": "x"}, headers=handler).status_code == 422
    assert dpdp.kit.incidents.get(inc["id"]).created_by == "handler"


def test_guardian_verification_flow(setup: tuple[TestClient, DPDPKit]) -> None:
    client, dpdp = setup
    dpdp.kit.guardians.register_verifier(
        CallbackVerifier("existing_kyc", lambda req, evidence: evidence.get("token") == "good")
    )
    child = {"X-User": "kid1"}
    start = "/dpdp/guardian/verify/start"
    assert client.post(start, json={"method": "existing_kyc"}).status_code == 401
    assert client.post(start, json={"method": "telepathy"}, headers=child).status_code == 422
    assert client.post(start, json={}, headers=child).status_code == 422
    assert client.post(start, json={"method": "court_order"}, headers=child).status_code == 422  # no verifier

    started = client.post(start, json={"method": "existing_kyc", "guardian_ref": "g1"}, headers=child)
    assert started.status_code == 202, started.text
    ver = started.json()
    assert ver["status"] == "pending" and ver["method"] == "existing_kyc" and ver["verified_at"] is None
    assert dpdp.kit.guardians.status("kid1") is GuardianStatus.PENDING

    callback = "/dpdp/guardian/verify/callback"
    body = {"verification_id": ver["id"], "token": "bad"}
    assert client.post(callback, json=body, headers={"X-User": "other"}).status_code == 404
    assert client.post(callback, json={**body, "verification_id": "nope"}, headers=child).status_code == 404
    failed = client.post(callback, json=body, headers=child)
    assert failed.status_code == 200 and failed.json()["status"] == "failed"
    assert client.post(callback, json=body, headers=child).status_code == 409  # already decided

    again = client.post(start, json={"method": "existing_kyc"}, headers=child).json()
    done = client.post(callback, json={"verification_id": again["id"], "token": "good"}, headers=child)
    assert done.status_code == 200 and done.json()["status"] == "verified" and done.json()["verified_at"]
    assert dpdp.kit.guardians.status("kid1") is GuardianStatus.VERIFIED


def test_guardian_start_refuses_a_confirmed_adult(setup: tuple[TestClient, DPDPKit]) -> None:
    client, dpdp = setup
    dpdp.kit.guardians.register_verifier(CallbackVerifier("existing_kyc", lambda req, evidence: True))
    dpdp.kit.guardians.assess_age("adult1", actor="gate", age=30)
    resp = client.post("/dpdp/guardian/verify/start", json={"method": "existing_kyc"}, headers={"X-User": "adult1"})
    assert resp.status_code == 409


def test_activity_middleware_cancels_warned_erasure(setup: tuple[TestClient, DPDPKit], clock: Clock) -> None:
    client, dpdp = setup
    kit = dpdp.kit
    kit.retention.record_activity("u1")
    clock.now += dt.timedelta(days=1095 - 1)
    kit.retention.run()
    warned = next(s for s in kit.retention.schedules("u1") if s.purpose == "account")
    assert warned.status is ScheduleStatus.WARNED
    assert client.get("/ping", headers={"X-User": "u1"}).status_code == 200
    assert kit.repository.get_schedule(kit.tenant_id, warned.id).status is ScheduleStatus.CANCELLED


def test_ledger_verify_endpoint_reports_tamper(setup: tuple[TestClient, DPDPKit]) -> None:
    client, dpdp = setup
    client.post(
        "/dpdp/consents", json={"decisions": [{"purpose": "marketing", "granted": True}]}, headers={"X-User": "u1"}
    )
    assert dpdp.verify_ledger_job() is True
    from sqlalchemy import text

    with dpdp.repository.engine.begin() as conn:  # type: ignore[attr-defined]
        conn.execute(text("UPDATE dpdp_consent_events SET principal = 'someone-else'"))
    body = client.post("/dpdp/admin/ledger/verify", headers={"X-Role": "viewer"}).json()
    assert body["ok"] is False and body["failed_row"]
    assert dpdp.verify_ledger_job() is False


def test_root_export_job_and_schedule(setup: tuple[TestClient, DPDPKit], tmp_path: Path) -> None:
    _, dpdp = setup
    sink = FileRootSink(tmp_path / "roots.jsonl")
    root = dpdp.export_root_job(sink)
    assert root is not None and sink.latest(dpdp.kit.tenant_id) == root
    dpdp.kit.ledger.check_root(root)

    class Broken:
        def write(self, root: RootHash) -> None:
            raise OSError("bucket unavailable")

    assert dpdp.export_root_job(Broken()) is None

    scheduler = dpdp.start_scheduler(root_sinks=[sink])
    try:
        assert {j.id for j in scheduler.get_jobs()} == {
            "dpdpkit-retention",
            "dpdpkit-ledger-verify",
            "dpdpkit-ledger-root-export",
        }
    finally:
        scheduler.shutdown(wait=False)


def test_admin_placeholder_is_served(setup: tuple[TestClient, DPDPKit]) -> None:
    client, _ = setup
    resp = client.get("/dpdp/admin/ui/")
    assert resp.status_code == 200 and "dpdpkit admin" in resp.text


def test_conformance_suite_passes_against_example() -> None:
    env = {**os.environ, "DPDPKIT_CONFORMANCE": "1", "DPDPKIT_ASGI_APP": "app:app", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "dpdpkit_conformance", "-q", "-p", "no:warnings"],
        cwd=EXAMPLE,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
