"""Adds ``dpdpkit migrate`` and ``dpdpkit webhooks`` to the core CLI (entry point group ``dpdpkit.cli``)."""

from __future__ import annotations

import argparse
from typing import Any


def _migrate(args: argparse.Namespace) -> int:
    from dpdpkit.cli import load_kit

    from .migrations import alembic_config, upgrade
    from .repository import SqlAlchemyRepository, make_engine

    if args.sql:
        from alembic import command

        command.upgrade(alembic_config(url=args.db_url or "postgresql://"), args.revision, sql=True)
        return 0
    if args.db_url:
        engine = make_engine(args.db_url)
    else:
        repo = load_kit(args.kit).repository
        if not isinstance(repo, SqlAlchemyRepository):
            print("error: the kit does not use the SQLAlchemy repository")
            return 1
        engine = repo.engine
    upgrade(engine, args.revision)
    print(f"database migrated to {args.revision}")
    return 0


def _load_dpdpkit(spec: str | None) -> Any:
    import importlib
    import os
    import sys

    from .app import DPDPKit

    spec = spec or os.environ.get("DPDPKIT_KIT")
    if not spec or ":" not in spec:
        raise SystemExit("error: pass --kit module:attribute (your DPDPKit) or set DPDPKIT_KIT")
    module_name, _, attr = spec.partition(":")
    sys.path.insert(0, os.getcwd())
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    if callable(obj) and not isinstance(obj, DPDPKit):
        obj = obj()
    if not isinstance(obj, DPDPKit):
        raise SystemExit(f"error: {spec} is not a DPDPKit")
    return obj


def _webhooks(args: argparse.Namespace) -> int:
    outbox = _load_dpdpkit(args.kit).webhooks
    if outbox is None:
        print("error: webhooks need the SQLAlchemy repository")
        return 1
    if args.webhooks_command == "retry":
        if not args.id and not args.all:
            print("error: pass --id DELIVERY_ID or --all")
            return 1
        count = outbox.redeliver(None if args.all else args.id)
        print(f"{count} delivery(ies) queued for redelivery")
        return 0
    if args.webhooks_command == "send":
        run = outbox.deliver_due()
        print(f"delivered {run.delivered}, retrying {run.retrying}, dead {run.dead}")
        return 0
    counts = outbox.counts()
    print(f"pending {counts['pending']}, delivered {counts['delivered']}, dead {counts['dead']}")
    status = "dead" if args.webhooks_command == "dead" else args.status
    for d in outbox.deliveries(status, limit=args.limit):
        print(
            f"{d.id}  {d.status:<9} {d.endpoint_id:<16} {d.event_type:<26} attempts={d.attempts}"
            + (f"  {d.last_error}" if d.last_error else "")
        )
    return 0


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("migrate", help="apply dpdpkit-fastapi database migrations")
    p.add_argument("--db-url", help="SQLAlchemy URL (or use --kit)")
    p.add_argument("--kit", help="module:attribute of your DPDPKit or Kit")
    p.add_argument("--revision", default="head")
    p.add_argument("--sql", action="store_true", help="print SQL instead of running it")
    p.set_defaults(func=_migrate)

    p = subparsers.add_parser("webhooks", help="inspect, send or redeliver outbound webhooks")
    p.add_argument("webhooks_command", choices=["list", "dead", "send", "retry"])
    p.add_argument("--kit", help="module:attribute of your DPDPKit")
    p.add_argument("--status", choices=["pending", "delivered", "dead"], help="list: filter by status")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--id", help="retry: one dead delivery")
    p.add_argument("--all", action="store_true", help="retry: every dead delivery")
    p.set_defaults(func=_webhooks)
