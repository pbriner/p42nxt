"""A fresh database per test session, set up the way the installer does it.

Needs the development PostgreSQL (`docker compose up -d`, port 5435). P42_TEST_ADMIN_URL points
elsewhere if needed.
"""

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.setup import set_up_database
from p42cloud.modules.core import install_core

ADMIN_URL = os.environ.get(
    "P42_TEST_ADMIN_URL", "postgresql+asyncpg://p42_admin:p42-admin-dev-only@localhost:5435/p42"
)
HOST = ADMIN_URL.rsplit("@", 1)[1].rsplit("/", 1)[0]


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[str]:
    name = f"p42_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield name
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture(scope="session")
def settings(database: str) -> Settings:
    return Settings(
        environment="test",
        database_url=f"postgresql+asyncpg://p42_app:p42-app-dev-only@{HOST}/{database}",
        owner_database_url=f"postgresql+asyncpg://p42_owner:p42-owner-dev-only@{HOST}/{database}",
    )


@pytest.fixture(scope="session")
async def installed(settings: Settings, database: str) -> Settings:
    await set_up_database(ADMIN_URL.rsplit("/", 1)[0] + f"/{database}", settings)
    return settings


@pytest.fixture
async def app_engine(installed: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(installed.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def admin_conn(database: str) -> AsyncIterator[AsyncEngine]:
    """An administrator connection to the test database, to break things on purpose."""
    engine = create_async_engine(
        ADMIN_URL.rsplit("/", 1)[0] + f"/{database}", isolation_level="AUTOCOMMIT"
    )
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def upgraded(installed: Settings) -> Settings:
    """The database set up and upgraded, the way the installer leaves it."""
    engine = create_engine(installed.owner_database_url)
    try:
        with working_as(Context(channel="upgrade")):
            await install_core(engine)
    finally:
        await engine.dispose()
    return installed


@pytest.fixture
async def owner_engine(upgraded: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def new_module(
    database: str, upgraded: Settings
) -> AsyncIterator[Callable[[], Awaitable[str]]]:
    """Makes modules with their own database role, as setting up a module will.

    Roles belong to the whole server, not the test database, so they're dropped at the end.
    """
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{database}"
    made: list[str] = []

    async def make() -> str:
        name = f"m{uuid.uuid4().hex[:10]}"
        admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE ROLE "p42_mod_{name}" NOLOGIN NOINHERIT'))
            made.append(f"p42_mod_{name}")
            await conn.execute(
                text(f'GRANT "p42_mod_{name}" TO p42_app WITH INHERIT FALSE, SET TRUE')
            )
        await admin.dispose()
        return name

    yield make
    admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        for role in made:
            await conn.execute(text(f'DROP OWNED BY "{role}"'))
            await conn.execute(text(f'DROP ROLE "{role}"'))
    await admin.dispose()
