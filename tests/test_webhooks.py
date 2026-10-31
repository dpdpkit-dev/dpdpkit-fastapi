from __future__ import annotations

import datetime as dt
import json
import os
import sys
import types
from pathlib import Path

import pytest

from dpdpkit.cli import main
from dpdpkit.errors import ConfigError, NotFound
from dpdpkit.events import WebhookEndpoint, verify_signature
from dpdpkit.fastapi import DPDPKit, SqlAlchemyRepository, WebhookOutbox
from dpdpkit.fastapi.db import Base

CONFIG = Path(__file__).resolve().parents[1] / "examples" / "fastapi_shop" / "dpdpkit.yaml"
T0 = dt.datetime(2027, 6, 1, tzinfo=dt.timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += dt.timedelta(**kwargs)


class Receiver:
    """Stands in for the customer's endpoint: answers with ``status`` (or raises ``error``)."""

    def __init__(self) -> None:
        self.status = 200
        self.error: Exception | None = None
        self.calls: list[tuple[str, bytes, dict[str, str]]] = []

    def __call__(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        self.calls.append((url, body, headers))
        if self.error:
            raise self.error
        return self.status


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def receiver() -> Receiver:
    return Receiver()


@pytest.fixture
def dpdp(clock: Clock, receiver: Receiver, tmp_path: Path) -> DPDPKit:
    """SQLite by default; CI sets DPDPKIT_TEST_DATABASE_URL for the Postgres and MySQL legs."""
    from sqlalchemy import text

    repo = SqlAlchemyRepository(os.environ.get("DPDPKIT_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'hooks.db'}")
    Base.metadata.drop_all(repo.engine)
    with repo.engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS dpdp_alembic_version"))
    kit = DPDPKit(
        repository=repo,
        config=CONFIG,
        principal_resolver=lambda r: None,
        signing_key="k",
        migrate_on_startup=True,
        clock=clock,
        webhooks=[
            WebhookEndpoint(id="crm", url="https://crm.example.in/hooks", secret="s1", events=["consent.withdrawn"]),
            WebhookEndpoint(id="all", url="https://ops.example.in/hooks", secret="s2"),
        ],
    )
    assert kit.webhooks is not None
    kit.webhooks.sender = receiver
    return kit


def outbox(dpdp: DPDPKit) -> WebhookOutbox:
    assert dpdp.webhooks is not None
    return dpdp.webhooks


def test_events_are_queued_not_sent_inline(dpdp: DPDPKit, receiver: Receiver) -> None:
    dpdp.kit.consent.grant("u1", "marketing")
    assert receiver.calls == []  # publishing never touches the network
    pending = outbox(dpdp).deliveries("pending")
    assert {d.endpoint_id for d in pending} == {"all"}  # crm only wants withdrawals
    dpdp.kit.consent.withdraw("u1", "marketing")
    assert {d.endpoint_id for d in outbox(dpdp).deliveries("pending") if d.event_type == "consent.withdrawn"} == {
        "crm",
        "all",
    }


def test_delivery_is_signed_and_marked_delivered(dpdp: DPDPKit, receiver: Receiver, clock: Clock) -> None:
    dpdp.kit.consent.grant("u1", "marketing")
    dpdp.kit.consent.withdraw("u1", "marketing")
    run = outbox(dpdp).deliver_due()
    assert run.delivered == len(receiver.calls) == outbox(dpdp).counts()["delivered"]
    _url, body, headers = next(c for c in receiver.calls if c[0].startswith("https://crm"))
    assert verify_signature("s1", body, headers["X-DPDPKit-Signature"])
    assert headers["X-DPDPKit-Event"] == "consent.withdrawn" and headers["X-DPDPKit-Delivery"].startswith("wh_")
    event = json.loads(body)
    assert event["type"] == "consent.withdrawn" and event["data"]["principal"] == "u1"
    assert outbox(dpdp).deliver_due().delivered == 0  # nothing is sent twice


def test_failures_back_off_then_dead_letter(dpdp: DPDPKit, receiver: Receiver, clock: Clock) -> None:
    box = outbox(dpdp)
    box.endpoints.pop("all")
    box.max_attempts = 3
    receiver.status = 503
    dpdp.kit.consent.grant("u1", "marketing")
    dpdp.kit.consent.withdraw("u1", "marketing")

    assert box.deliver_due().retrying == 1
    [d] = box.deliveries("pending")
    assert d.attempts == 1 and d.next_attempt_at == T0 + dt.timedelta(seconds=30) and d.last_error == "HTTP 503"
    clock.advance(seconds=29)
    assert box.deliver_due().retrying == 0  # not due yet
    clock.advance(seconds=1)
    receiver.error = ConnectionError("refused")
    assert box.deliver_due().retrying == 1
    [d] = box.deliveries("pending")
    assert d.next_attempt_at == clock.now + dt.timedelta(seconds=60) and "refused" in (d.last_error or "")
    clock.advance(seconds=60)
    assert box.deliver_due().dead == 1
    [dead] = box.dead_letters()
    assert dead.attempts == 3 and len(receiver.calls) == 3

    receiver.error, receiver.status = None, 200
    assert box.redeliver(dead.id) == 1
    assert box.deliver_due().delivered == 1
    assert box.counts() == {"pending": 0, "delivered": 1, "dead": 0}
    with pytest.raises(NotFound):
        box.redeliver(dead.id)


def test_backoff_is_capped(dpdp: DPDPKit) -> None:
    box = outbox(dpdp)
    assert [box.backoff(n).total_seconds() for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert box.backoff(20) == dt.timedelta(hours=2)


def test_claimed_row_is_not_sent_by_a_second_worker(dpdp: DPDPKit, receiver: Receiver) -> None:
    box = outbox(dpdp)
    dpdp.kit.consent.grant("u1", "marketing")
    [row] = box.deliveries("pending")
    from dpdpkit.fastapi.db import WebhookDeliveryRow

    with box.repository.Session() as s:
        stale = s.get(WebhookDeliveryRow, row.id)
    assert stale is not None
    assert box._claim(stale, T0) is True
    assert box._claim(stale, T0) is False  # same snapshot, already claimed
    assert box.deliver_due().delivered == 0  # leased for 5 minutes
    assert receiver.calls == []


def test_removed_endpoint_goes_straight_to_dead_letters(dpdp: DPDPKit, receiver: Receiver) -> None:
    dpdp.kit.consent.grant("u1", "marketing")
    outbox(dpdp).endpoints.pop("all")
    assert outbox(dpdp).deliver_due().dead == 1
    assert "not configured" in (outbox(dpdp).dead_letters()[0].last_error or "")
    assert receiver.calls == []


def test_endpoints_from_config_read_secrets_from_env(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    import yaml

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["webhooks"] = [{"id": "erp", "url": "https://erp.example.in/hook", "secret_env": "ERP_HOOK_SECRET"}]
    with pytest.raises(ConfigError, match="ERP_HOOK_SECRET"):
        DPDPKit(db_url="sqlite://", config=config, principal_resolver=lambda r: None, create_tables=True)
    monkeypatch.setenv("ERP_HOOK_SECRET", "from-env")
    dpdp = DPDPKit(db_url="sqlite://", config=config, principal_resolver=lambda r: None, create_tables=True)
    assert outbox(dpdp).endpoints["erp"].secret == "from-env"
    config["webhooks"] = [{"id": "erp", "url": "https://erp.example.in/hook", "secret": "inline"}]
    with pytest.raises(ConfigError, match="secret_env"):
        DPDPKit(db_url="sqlite://", config=config, principal_resolver=lambda r: None, create_tables=True)


def test_scheduler_runs_deliveries(dpdp: DPDPKit) -> None:
    scheduler = dpdp.start_scheduler(webhook_seconds=10)
    try:
        assert "dpdpkit-webhooks" in {j.id for j in scheduler.get_jobs()}
    finally:
        scheduler.shutdown(wait=False)


def test_cli(dpdp: DPDPKit, receiver: Receiver, capsys: pytest.CaptureFixture[str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    module = types.ModuleType("hooks_app")
    module.dpdp = dpdp  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hooks_app", module)
    outbox(dpdp).max_attempts = 1
    receiver.status = 500
    dpdp.kit.consent.grant("u1", "marketing")
    assert main(["webhooks", "send", "--kit", "hooks_app:dpdp"]) == 0
    assert "dead 1" in capsys.readouterr().out
    assert main(["webhooks", "dead", "--kit", "hooks_app:dpdp"]) == 0
    assert "HTTP 500" in capsys.readouterr().out
    assert main(["webhooks", "retry", "--kit", "hooks_app:dpdp"]) == 1
    receiver.status = 200
    assert main(["webhooks", "retry", "--all", "--kit", "hooks_app:dpdp"]) == 0
    assert main(["webhooks", "send", "--kit", "hooks_app:dpdp"]) == 0
    assert "delivered 1" in capsys.readouterr().out
    assert main(["webhooks", "list", "--status", "delivered", "--kit", "hooks_app:dpdp"]) == 0


def test_non_utc_clock_is_normalised(dpdp: DPDPKit, receiver: Receiver, clock: Clock) -> None:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    clock.now = T0.astimezone(ist)
    receiver.status = 500
    outbox(dpdp).endpoints.pop("crm")
    dpdp.kit.consent.grant("u1", "marketing")
    assert outbox(dpdp).deliver_due().retrying == 1
    [d] = outbox(dpdp).deliveries("pending")
    assert d.next_attempt_at == T0 + dt.timedelta(seconds=30) and d.next_attempt_at.tzinfo is dt.timezone.utc
    clock.advance(seconds=29)
    assert outbox(dpdp).deliver_due().retrying == 0
    clock.advance(seconds=1)
    assert outbox(dpdp).deliver_due().retrying == 1
