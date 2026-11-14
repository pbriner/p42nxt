"""Testing a module in its own folder: a pytest plugin with a fresh installation.

In the module's `tests/conftest.py`:

    pytest_plugins = ["p42cloud.sdk.testing"]

    @pytest.fixture(scope="session")
    def p42_modules() -> list[Module]:
        return [module]

Each test session gets its own database, set up the way the installer does it, with the core
and these modules installed. `as_system()` and `as_person(id)` give a `Client` for one request
(committed at the end); `run_job(name)` runs a job as the worker would and returns its state and
what it said; `call_route(person, method, address)` calls a module route as that person;
`stand_in(type, answer)` answers for an outside system; `deliver_calls()` sends the outside
calls that are due. Needs the development PostgreSQL (`docker compose up -d`);
`P42_TEST_ADMIN_URL` points elsewhere. The modules' database roles are kept between sessions:
they belong to the whole server and own nothing.
"""

import os
import re
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.secrets import Sealer, use_sealer
from p42cloud.kernel.setup import set_up_database
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.sdk.client import Client
from p42cloud.security.access.service import access_for
from p42cloud.services.calls.service import deliver
from p42cloud.services.connections.service import TEST_TRANSPORTS
from p42cloud.services.jobs.service import run_job as run_now
from p42cloud.services.routes.models import RouteRequest
from p42cloud.services.routes.service import ROUTES

ADMIN_URL = os.environ.get(
    "P42_TEST_ADMIN_URL", "postgresql+asyncpg://p42_admin:p42-admin-dev-only@localhost:5435/p42"
)
_SERVER = ADMIN_URL.rsplit("/", 1)[0]
_HOST = ADMIN_URL.rsplit("@", 1)[1].rsplit("/", 1)[0]

type Acting = Callable[..., AbstractAsyncContextManager[Client]]


@pytest.fixture(scope="session")
def p42_modules() -> list[Module]:
    """The modules to install besides the core; a module's tests override this."""
    return []


@pytest.fixture(scope="session")
async def p42_installation(p42_modules: list[Module]) -> AsyncIterator[Settings]:
    """A fresh database with the core and the modules installed; dropped at the end."""
    name = f"p42_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    settings = Settings(
        environment="test",
        database_url=f"postgresql+asyncpg://p42_app:p42-app-dev-only@{_HOST}/{name}",
        owner_database_url=f"postgresql+asyncpg://p42_owner:p42-owner-dev-only@{_HOST}/{name}",
    )
    try:
        await set_up_database(f"{_SERVER}/{name}", settings)
        owner = create_engine(settings.owner_database_url)
        try:
            with working_as(Context(channel="install")):
                await install_modules(owner, [CORE, *p42_modules])
        finally:
            await owner.dispose()
        yield settings
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture(scope="session")
async def p42_engine(p42_installation: Settings) -> AsyncIterator[AsyncEngine]:
    """The app's connection to the test installation."""
    engine = create_engine(p42_installation.database_url)
    yield engine
    await engine.dispose()


@asynccontextmanager
async def _request(engine: AsyncEngine, context: Context) -> AsyncGenerator[Client]:
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            yield Client(Records(conn, await load_catalog(conn)))


@pytest.fixture
def as_system(p42_engine: AsyncEngine) -> Acting:
    """One request by the system itself: everything allowed."""

    def acting() -> AbstractAsyncContextManager[Client]:
        return _request(p42_engine, Context(channel="test", access=EVERYTHING))

    return acting


@pytest.fixture
def as_person(p42_engine: AsyncEngine) -> Acting:
    """One request by a person, with the rights their groups give them, as if they had just
    given their second factor."""

    @asynccontextmanager
    async def acting(person: uuid.UUID | str) -> AsyncGenerator[Client]:
        who = person if isinstance(person, uuid.UUID) else uuid.UUID(person)
        with working_as(Context(channel="test", person_id=who)):
            async with transaction(p42_engine, "sys") as conn:
                access = await access_for(conn, who)
        context = Context(
            channel="test", person_id=who, access=access, verified_at=datetime.now(UTC)
        )
        async with _request(p42_engine, context) as client:
            yield client

    return acting


type RunJob = Callable[[str], Awaitable[tuple[str, str | None]]]


@pytest.fixture
def run_job(p42_engine: AsyncEngine) -> RunJob:
    """Runs a job (`<module>.<name>`) at once, as its module's service account, like the worker:
    returns its state (succeeded, failed, skipped) and what it said."""

    async def running(name: str) -> tuple[str, str | None]:
        return await run_now(p42_engine, name)

    return running


type CallRoute = Callable[..., Awaitable[Any]]


@pytest.fixture
def call_route(p42_engine: AsyncEngine) -> CallRoute:
    """Calls a module route as a person, the way the web app would (minus HTTP):
    `await call_route(person, "GET", "/hello/widget/languages", query={...})`."""

    async def calling(
        person: uuid.UUID | str,
        method: str,
        address: str,
        *,
        query: dict[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        module, _, rest = address.lstrip("/").partition("/")
        for owner, route in ROUTES:
            if owner != module or route.method != method:
                continue
            pattern = "^" + re.sub(r"\{([a-z0-9_]+)\}", r"(?P<\1>[^/]+)", route.path) + "$"
            found = re.match(pattern, f"/{rest}")
            if found is None:
                continue
            who = person if isinstance(person, uuid.UUID) else uuid.UUID(person)
            with working_as(Context(channel="test", person_id=who)):
                async with transaction(p42_engine, "sys") as conn:
                    access = await access_for(conn, who)
            context = Context(channel="test", person_id=who, access=access)
            with working_as(context):
                async with transaction(p42_engine, "sys") as conn:
                    records = Records(conn, await load_catalog(conn))
                    return await route.handler(
                        RouteRequest(records, who, found.groupdict(), query or {}, body)
                    )
        raise LookupError(f"No route {method} {address}")

    return calling


type StandIn = Callable[[str, Callable[[httpx.Request], httpx.Response]], None]


@pytest.fixture
def stand_in() -> Iterator[StandIn]:
    """Answers a connection type's requests with the test's own function instead of the outside
    system: `stand_in("dca.kraken", answer)`. Secrets are sealed with a key made for the test."""
    use_sealer(Sealer(os.urandom(32)))
    named: list[str] = []

    def standing_in(type_name: str, answer: Callable[[httpx.Request], httpx.Response]) -> None:
        TEST_TRANSPORTS[type_name] = httpx.MockTransport(answer)
        named.append(type_name)

    yield standing_in
    for type_name in named:
        TEST_TRANSPORTS.pop(type_name, None)


type DeliverCalls = Callable[..., Awaitable[list[str]]]


@pytest.fixture
def deliver_calls(p42_engine: AsyncEngine) -> DeliverCalls:
    """Delivers the outside calls that are due, as the worker would: their states in turn.
    `later=True` acts as if their waits were over."""

    async def delivering(*, later: bool = False) -> list[str]:
        moment = datetime.now(UTC) + (timedelta(days=1) if later else timedelta(0))
        states: list[str] = []
        while (state := await deliver(p42_engine, moment)) is not None:
            states.append(state)
        return states

    return delivering
