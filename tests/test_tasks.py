from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from dpdpkit.events import WebhookEndpoint
from dpdpkit.fastapi import DPDPKit, SqlAlchemyRepository
from dpdpkit.fastapi.tasks import (
    DELIVER_WEBHOOKS,
    EXPORT_ROOT,
    RETENTION,
    VERIFY_LEDGER,
    arq_cron_jobs,
    arq_functions,
    build_jobs,
    register_celery,
)

CONFIG = Path(__file__).resolve().parents[1] / "examples" / "fastapi_shop" / "dpdpkit.yaml"


def make(tmp_path: Path, webhooks: bool = False) -> DPDPKit:
    return DPDPKit(
        repository=SqlAlchemyRepository(f"sqlite:///{tmp_path / 'tasks.db'}"),
        config=CONFIG,
        principal_resolver=lambda r: None,
        signing_key="k",
        migrate_on_startup=True,
        webhooks=[WebhookEndpoint(id="a", url="https://a.example.in/h", secret="s")] if webhooks else [],
    )


class FakeCelery:
    def __init__(self) -> None:
        self.conf = types.SimpleNamespace(beat_schedule={"mine": {"task": "mine"}})
        self.registered: dict[str, Any] = {}

    def task(self, name: str) -> Any:
        def deco(fn: Any) -> Any:
            self.registered[name] = fn
            return fn

        return deco


def test_jobs_return_json_summaries(tmp_path: Path) -> None:
    dpdp = make(tmp_path, webhooks=True)
    assert dpdp.webhooks is not None
    dpdp.webhooks.sender = lambda url, body, headers: 200
    jobs = build_jobs(dpdp)
    assert set(jobs) == {RETENTION, VERIFY_LEDGER, EXPORT_ROOT, DELIVER_WEBHOOKS}
    assert isinstance(jobs[RETENTION](), dict)
    assert jobs[VERIFY_LEDGER]() == {"ok": True}
    assert jobs[EXPORT_ROOT]()["exported"] is True
    # the root export above queued a ledger.root_exported event for the catch-all endpoint
    assert jobs[DELIVER_WEBHOOKS]() == {"delivered": 1, "retrying": 0, "dead": 0}


def test_webhook_job_omitted_without_endpoints(tmp_path: Path) -> None:
    assert DELIVER_WEBHOOKS not in build_jobs(make(tmp_path))


def test_register_celery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    schedules = types.ModuleType("celery.schedules")
    schedules.crontab = lambda **kw: ("cron", kw)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "celery", types.ModuleType("celery"))
    monkeypatch.setitem(sys.modules, "celery.schedules", schedules)
    app = FakeCelery()
    tasks = register_celery(app, make(tmp_path))
    assert set(tasks) == set(app.registered) == {RETENTION, VERIFY_LEDGER, EXPORT_ROOT}
    beat = app.conf.beat_schedule
    assert "mine" in beat
    assert beat[RETENTION] == {"task": RETENTION, "schedule": 900.0}
    assert beat[VERIFY_LEDGER]["schedule"] == ("cron", {"hour": 2, "minute": 0})
    assert app.registered[VERIFY_LEDGER]() == {"ok": True}


def test_register_celery_without_beat(tmp_path: Path) -> None:
    app = FakeCelery()
    register_celery(app, make(tmp_path), beat=False)
    assert app.conf.beat_schedule == {"mine": {"task": "mine"}}


def test_arq_functions_run_jobs(tmp_path: Path) -> None:
    funcs = arq_functions(make(tmp_path))
    assert [f.__name__ for f in funcs] == ["dpdpkit_retention", "dpdpkit_verify_ledger", "dpdpkit_export_root"]
    assert asyncio.run(funcs[1]({})) == {"ok": True}


def test_arq_cron_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    arq = types.ModuleType("arq")
    arq.cron = lambda fn, **kw: (fn.__name__, kw)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "arq", arq)
    jobs = dict(arq_cron_jobs(make(tmp_path, webhooks=True)))
    assert jobs["dpdpkit_retention"]["minute"] == {0, 15, 30, 45}
    assert jobs["dpdpkit_verify_ledger"] == {"hour": 2, "minute": 0, "second": 0}
    assert jobs["dpdpkit_deliver_webhooks"]["second"] == {0, 30}
