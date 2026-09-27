from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from dpdpkit.fastapi.db import Base
from dpdpkit.fastapi.migrations import include_name

config = context.config
target_metadata = Base.metadata
version_table = config.get_main_option("version_table") or "dpdp_alembic_version"


def run_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=version_table,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    engine = config.attributes.get("engine") or create_engine(str(config.get_main_option("sqlalchemy.url")))
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=version_table,
            include_name=include_name,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
