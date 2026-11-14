"""The web installer: open only until there's an administrator, and only with the setup code."""

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.secrets import Sealer
from p42cloud.kernel.setup import set_up_database
from p42cloud.modules.core import install_core
from p42cloud.web.app import create_app
from tests.conftest import ADMIN_URL, HOST

SITE = "https://p42.test"
CODE = "AB12CD34"


def _body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": CODE,
        "language": "de",
        "name": "Acme",
        "admin_name": "Ada",
        "email": "ada@acme.test",
        "password": "correct horse battery staple",
        "modules": [],
        "backups": False,
        "master_key": True,
    }
    return body | changes


@pytest.fixture(scope="module")
async def fresh() -> AsyncIterator[Settings]:
    """An installation nobody has set up yet."""
    name = f"p42_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    settings = Settings(
        environment="test",
        database_url=f"postgresql+asyncpg://p42_app:p42-app-dev-only@{HOST}/{name}",
        owner_database_url=f"postgresql+asyncpg://p42_owner:p42-owner-dev-only@{HOST}/{name}",
        public_url=SITE,
        setup_code=CODE,
    )
    try:
        await set_up_database(ADMIN_URL.rsplit("/", 1)[0] + f"/{name}", settings)
        owner = create_engine(settings.owner_database_url)
        with working_as(Context(channel="upgrade")):
            await install_core(owner)
        await owner.dispose()
        yield settings
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


async def _client(settings: Settings) -> tuple[Any, httpx.AsyncClient]:
    app = create_app(settings, sealer=Sealer(os.urandom(32)), upgrade=False)
    return app, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=SITE)


async def test_the_installer_is_open_once_and_only_with_its_code(fresh: Settings) -> None:
    app, client = await _client(fresh)
    async with app.router.lifespan_context(app), client:
        before = (await client.get("/api/v1/installation")).json()
        modules = (await client.get("/api/v1/setup/modules")).json()["modules"]
        wrong = await client.post("/api/v1/setup", json=_body(code="nope"))
        unconfirmed = await client.post("/api/v1/setup", json=_body(master_key=False))
        weak = await client.post("/api/v1/setup", json=_body(password="short"))
        done = await client.post("/api/v1/setup", json=_body())
        again = await client.post("/api/v1/setup", json=_body(email="eve@acme.test"))
        after = (await client.get("/api/v1/installation")).json()
        closed = await client.get("/api/v1/setup/modules")
    assert before == {"name": "P42cloud", "language": "en", "setup_open": True}
    assert any(m["id"] == "sys" and m["required"] for m in modules)
    assert (wrong.status_code, wrong.json()["code"]) == (403, "wrong_setup_code")
    assert unconfirmed.json()["code"] == "confirm"
    assert weak.json()["code"] == "weak_password"
    assert done.json() == {"stage": "enroll"}  # signed in up to setting up the second factor
    assert "p42_session" in done.headers["set-cookie"]
    assert (again.status_code, again.json()["code"]) == (409, "set_up")
    assert after == {"name": "Acme", "language": "de", "setup_open": False}
    assert closed.status_code == 409


async def test_without_a_setup_code_the_installer_stays_closed(fresh: Settings) -> None:
    app, client = await _client(fresh.model_copy(update={"setup_code": None}))
    async with app.router.lifespan_context(app), client:
        found = (await client.get("/api/v1/installation")).json()
        refused = await client.post("/api/v1/setup", json=_body())
    assert found["setup_open"] is False
    assert refused.json()["code"] == "no_setup_code"
