from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from dpdpkit import Contact, MemoryNotifier
from dpdpkit.fastapi import DPDPKit, require_consent
from dpdpkit.models import ScheduleStatus

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


def test_incidents_not_yet_implemented(setup: tuple[TestClient, DPDPKit]) -> None:
    client, _ = setup
    resp = client.get("/dpdp/admin/incidents", headers={"X-Role": "admin"})
    assert resp.status_code == 501 and resp.json()["error"]["code"] == "not_implemented"


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
