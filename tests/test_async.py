from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from dpdpkit.errors import ConfigError
from dpdpkit.fastapi import DPDPKit, SqlAlchemyRepository, require_consent
from dpdpkit.fastapi.repository import enable_sqlite_savepoints, sync_url

CONFIG = Path(__file__).resolve().parents[1] / "examples" / "fastapi_shop" / "dpdpkit.yaml"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgresql+asyncpg://u:p%40ss@db:5432/app", "postgresql+psycopg://u:p%40ss@db:5432/app"),
        ("postgresql+psycopg://u:p@db/app", "postgresql+psycopg://u:p@db/app"),
        ("mysql+aiomysql://u:p@db/app", "mysql+pymysql://u:p@db/app"),
        ("mysql+asyncmy://u:p@db/app", "mysql+pymysql://u:p@db/app"),
        ("sqlite+aiosqlite:///app.db", "sqlite:///app.db"),
        ("sqlite://", "sqlite://"),
    ],
)
def test_async_urls_map_to_sync_drivers(given: str, expected: str) -> None:
    assert sync_url(given) == expected  # passwords survive, including escaped characters


def test_unknown_async_driver_is_refused() -> None:
    with pytest.raises(ConfigError, match="no sync driver"):
        sync_url("postgresql+asyncsomething://db/app")
    with pytest.raises(ConfigError, match="unsupported engine"):
        SqlAlchemyRepository(42)


def test_shares_an_async_apps_database(tmp_path: Path) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    app_engine = create_async_engine(url)
    dpdp = DPDPKit(
        engine=app_engine,
        config=CONFIG,
        principal_resolver=lambda r: r.headers.get("X-User"),
        signing_key="k",
        migrate_on_startup=True,
    )
    dpdp.kit.consent.grant("u1", "marketing")

    async def read_with_the_app_engine() -> int:
        async with app_engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM dpdp_consent_events"))
            return int(result.scalar_one())

    assert asyncio.run(read_with_the_app_engine()) == 1
    dpdp2 = DPDPKit(db_url=url, config=CONFIG, principal_resolver=lambda r: None, signing_key="k")
    assert dpdp2.kit.consent.check("u1", "marketing") is True


def test_run_calls_kit_methods_from_async_code(tmp_path: Path) -> None:
    dpdp = DPDPKit(
        db_url=f"sqlite:///{tmp_path / 'a.db'}",
        config=CONFIG,
        principal_resolver=lambda r: r.headers.get("X-User"),
        signing_key="k",
        migrate_on_startup=True,
    )
    app = FastAPI()
    dpdp.install(app)

    @app.post("/newsletter")
    async def newsletter(user: str) -> dict[str, bool]:
        allowed = await dpdp.run(dpdp.kit.consent.check, user, "marketing")
        return {"sent": allowed}

    @app.post("/offers", dependencies=[Depends(require_consent("marketing"))])
    async def offers() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.post("/newsletter", params={"user": "u1"}).json() == {"sent": False}
    asyncio.run(dpdp.run(dpdp.kit.consent.grant, "u1", "marketing"))
    assert client.post("/newsletter", params={"user": "u1"}).json() == {"sent": True}
    assert client.post("/offers", headers={"X-User": "u1"}).status_code == 200


# ---------------------------------------------------------------------- app sessions


class AppBase(DeclarativeBase):
    pass


class Order(AppBase):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    item: Mapped[str]


def _kit(engine: Any) -> DPDPKit:
    return DPDPKit(
        engine=engine,
        config=CONFIG,
        principal_resolver=lambda r: r.headers.get("X-User"),
        signing_key="k",
        migrate_on_startup=True,
    )


def test_run_in_session_commits_and_rolls_back_with_the_app(tmp_path: Path) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    app_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    dpdp = _kit(app_engine)

    async def scenario() -> None:
        async with app_engine.begin() as conn:
            await conn.run_sync(AppBase.metadata.create_all)

        async with AsyncSession(app_engine) as s, s.begin():
            s.add(Order(item="book"))
            await dpdp.run_in_session(s, dpdp.kit.consent.grant, "u1", "marketing")
            # Visible inside the app's transaction, before it commits.
            assert await dpdp.run_in_session(s, dpdp.kit.consent.check, "u1", "marketing") is True

        class Boom(Exception):
            pass

        with pytest.raises(Boom):
            async with AsyncSession(app_engine) as s, s.begin():
                # dpdpkit's savepoint is the first write in the transaction; the rollback still undoes it.
                await dpdp.run_in_session(s, dpdp.kit.consent.withdraw, "u1", "marketing")
                s.add(Order(item="pen"))
                raise Boom

        async with AsyncSession(app_engine) as s:
            assert list(await s.scalars(select(Order.item))) == ["book"]
        await app_engine.dispose()

    asyncio.run(scenario())
    assert dpdp.kit.consent.check("u1", "marketing") is True
    assert [e.action.value for e in dpdp.repository.list_consent_events("default")] == ["grant"]
    dpdp.kit.consent.withdraw("u1", "marketing")  # the chain continues from the committed event
    assert dpdp.kit.ledger.verify().ok


def test_bind_joins_a_sync_session(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    dpdp = _kit(engine)
    AppBase.metadata.create_all(engine)
    enable_sqlite_savepoints(engine)

    with pytest.raises(RuntimeError), Session(engine) as s, s.begin(), dpdp.bind(s):
        s.add(Order(item="book"))
        dpdp.kit.consent.grant("u1", "marketing")
        raise RuntimeError
    assert dpdp.kit.consent.check("u1", "marketing") is False

    with Session(engine) as s, s.begin(), dpdp.bind(s):
        s.add(Order(item="book"))
        dpdp.kit.consent.grant("u1", "marketing")
        with pytest.raises(ConfigError, match="cannot be nested"), dpdp.bind(s):
            pass
    assert dpdp.kit.consent.check("u1", "marketing") is True
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 1


def test_bound_session_is_not_shared_with_other_tasks(tmp_path: Path) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    app_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    dpdp = _kit(app_engine)
    repo = dpdp.repository
    assert isinstance(repo, SqlAlchemyRepository)
    inside: list[Session] = []
    seen: list[object] = []

    def hold() -> None:
        bound = repo._current.get()
        assert bound is not None
        inside.append(bound)
        for _ in range(100):  # each query yields to the event loop, letting the other task run
            if seen:
                return
            bound.execute(text("SELECT 1"))

    async def other() -> None:
        while not inside:
            await asyncio.sleep(0)
        seen.append(repo._current.get())

    async def scenario() -> None:
        async with AsyncSession(app_engine) as s:
            await asyncio.gather(dpdp.run_in_session(s, hold), other())
        await app_engine.dispose()

    asyncio.run(asyncio.wait_for(scenario(), 10))
    assert seen == [None]


def test_binding_needs_the_sql_repository() -> None:
    from dpdpkit.repository import InMemoryRepository

    dpdp = DPDPKit(repository=InMemoryRepository(), config=CONFIG, principal_resolver=lambda r: None, signing_key="k")
    with pytest.raises(ConfigError, match="SqlAlchemyRepository"):
        dpdp.bind(Session())
