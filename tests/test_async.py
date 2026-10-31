from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from dpdpkit.errors import ConfigError
from dpdpkit.fastapi import DPDPKit, SqlAlchemyRepository, require_consent
from dpdpkit.fastapi.repository import sync_url

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
    from sqlalchemy import text
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
