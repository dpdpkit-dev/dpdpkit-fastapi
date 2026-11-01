"""Celery and Arq entry points for the periodic jobs, for teams that already run a worker.

The jobs are the ones ``DPDPKit.start_scheduler()`` runs: retention, ledger verification, root-hash export
and webhook delivery. Each returns a small JSON-safe summary so it can be stored as a task result::

    celery_app = Celery("shop", broker="redis://localhost")
    register_celery(celery_app, dpdp)          # tasks + beat schedule

    class WorkerSettings:                      # arq
        functions = arq_functions(dpdp)
        cron_jobs = arq_cron_jobs(dpdp)

Neither Celery nor Arq is imported until you call these helpers. Run a single beat/cron process: the jobs
are safe to repeat, but running them from several schedulers only wastes work.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from dpdpkit.errors import ConfigError
from dpdpkit.ledger import RootSink

if TYPE_CHECKING:
    from .app import DPDPKit

JobResult = dict[str, Any]

RETENTION = "dpdpkit.retention"
VERIFY_LEDGER = "dpdpkit.verify_ledger"
EXPORT_ROOT = "dpdpkit.export_root"
DELIVER_WEBHOOKS = "dpdpkit.deliver_webhooks"

# Default cadence, matching start_scheduler(): (task name, cron fields as UTC minute/hour or interval seconds).
_DEFAULT_SCHEDULE: dict[str, dict[str, int]] = {
    RETENTION: {"every_seconds": 15 * 60},
    VERIFY_LEDGER: {"hour": 2, "minute": 0},
    EXPORT_ROOT: {"hour": 3, "minute": 0},
    DELIVER_WEBHOOKS: {"every_seconds": 30},
}


def build_jobs(dpdp: DPDPKit, root_sinks: Iterable[RootSink] = ()) -> dict[str, Callable[[], JobResult]]:
    """The jobs keyed by task name. Webhook delivery is omitted when no endpoint is configured."""
    sinks = tuple(root_sinks)

    def retention() -> JobResult:
        return dpdp.kit.retention.run().model_dump(mode="json")

    def verify_ledger() -> JobResult:
        return {"ok": dpdp.verify_ledger_job()}

    def export_root() -> JobResult:
        root = dpdp.export_root_job(*sinks)
        return {"exported": root is not None, "root": root.model_dump(mode="json") if root else None}

    jobs: dict[str, Callable[[], JobResult]] = {
        RETENTION: retention,
        VERIFY_LEDGER: verify_ledger,
        EXPORT_ROOT: export_root,
    }
    if dpdp.webhooks is not None and dpdp.webhooks.endpoints:
        outbox = dpdp.webhooks

        def deliver_webhooks() -> JobResult:
            return dataclasses.asdict(outbox.deliver_due())

        jobs[DELIVER_WEBHOOKS] = deliver_webhooks
    return jobs


# ---------------------------------------------------------------------- Celery


def register_celery(
    celery_app: Any, dpdp: DPDPKit, *, root_sinks: Iterable[RootSink] = (), beat: bool = True
) -> dict[str, Any]:
    """Register the jobs as Celery tasks named ``dpdpkit.*`` and, with ``beat=True``, add them to
    ``celery_app.conf.beat_schedule`` (existing entries are kept). Returns the tasks by name."""
    jobs = build_jobs(dpdp, root_sinks)
    tasks = {name: celery_app.task(name=name)(fn) for name, fn in jobs.items()}
    if beat:
        try:
            from celery.schedules import crontab  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise ConfigError("install Celery to use register_celery(): pip install celery") from exc
        schedule = dict(celery_app.conf.beat_schedule or {})
        for name in jobs:
            when = _DEFAULT_SCHEDULE[name]
            schedule[name] = {
                "task": name,
                "schedule": float(when["every_seconds"])
                if "every_seconds" in when
                else crontab(hour=when["hour"], minute=when["minute"]),
            }
        celery_app.conf.beat_schedule = schedule
    return tasks


# ---------------------------------------------------------------------- Arq


def arq_functions(dpdp: DPDPKit, root_sinks: Iterable[RootSink] = ()) -> list[Callable[..., Any]]:
    """Coroutine functions for ``WorkerSettings.functions``. Each takes arq's ``ctx`` and runs the job in a
    worker thread, so the database work does not block the worker's event loop."""
    return list(_arq_by_name(dpdp, root_sinks).values())


def arq_cron_jobs(dpdp: DPDPKit, root_sinks: Iterable[RootSink] = ()) -> list[Any]:
    """``arq.cron`` jobs for ``WorkerSettings.cron_jobs`` at the same cadence as ``start_scheduler()``."""
    try:
        from arq import cron  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise ConfigError("install Arq to use arq_cron_jobs(): pip install arq") from exc
    out = []
    for name, fn in _arq_by_name(dpdp, root_sinks).items():
        when = _DEFAULT_SCHEDULE[name]
        if "every_seconds" in when:
            seconds = when["every_seconds"]
            out.append(
                cron(
                    fn,
                    minute=None if seconds < 60 else set(range(0, 60, seconds // 60)),
                    second=set(range(0, 60, seconds)) if seconds < 60 else 0,
                    run_at_startup=False,
                )
            )
        else:
            out.append(cron(fn, hour=when["hour"], minute=when["minute"], second=0))
    return out


def _arq_by_name(dpdp: DPDPKit, root_sinks: Iterable[RootSink]) -> dict[str, Callable[..., Any]]:
    return {name: _arq_function(dpdp, name, job) for name, job in build_jobs(dpdp, root_sinks).items()}


def _arq_function(dpdp: DPDPKit, name: str, job: Callable[[], JobResult]) -> Callable[..., Any]:
    async def run(ctx: dict[str, Any]) -> JobResult:
        return await dpdp.run(job)

    run.__name__ = name.replace(".", "_", 1)
    run.__qualname__ = run.__name__
    return run
