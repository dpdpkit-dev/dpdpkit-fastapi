"""Adds ``dpdpkit migrate`` to the core CLI (entry point group ``dpdpkit.cli``)."""

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


def register(subparsers: Any) -> None:
    p = subparsers.add_parser("migrate", help="apply dpdpkit-fastapi database migrations")
    p.add_argument("--db-url", help="SQLAlchemy URL (or use --kit)")
    p.add_argument("--kit", help="module:attribute of your DPDPKit or Kit")
    p.add_argument("--revision", default="head")
    p.add_argument("--sql", action="store_true", help="print SQL instead of running it")
    p.set_defaults(func=_migrate)
