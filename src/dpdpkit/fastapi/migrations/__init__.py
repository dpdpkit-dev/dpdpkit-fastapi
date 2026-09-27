"""Alembic migrations shipped inside the package. Forward-only; they never drop consent or audit data."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

HERE = Path(__file__).parent
VERSION_TABLE = "dpdp_alembic_version"


def include_name(name: str | None, type_: str, _parent: object) -> bool:
    """dpdpkit shares the host app's database: migrations only ever consider its own tables."""
    if type_ == "table":
        return bool(name) and str(name).startswith("dpdp_") and name != VERSION_TABLE
    return True


def alembic_config(engine: Engine | None = None, url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.set_main_option("version_table", VERSION_TABLE)
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if engine is not None:
        cfg.attributes["engine"] = engine
    return cfg


def upgrade(engine: Engine, revision: str = "head") -> None:
    command.upgrade(alembic_config(engine), revision)
