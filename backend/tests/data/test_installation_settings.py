"""The installation's settings that were environment variables: checked, taken over once, and
read while running."""

import uuid
from collections.abc import AsyncGenerator, Iterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data import installation
from p42cloud.data.settings import Setting, write
from p42cloud.data.types import Invalid
from p42cloud.kernel import outgoing
from p42cloud.kernel.config import Settings, get_settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction


@pytest.fixture
def production(monkeypatch: pytest.MonkeyPatch, upgraded: Settings) -> Iterator[None]:
    monkeypatch.setenv("P42_DATABASE_URL", upgraded.database_url)
    monkeypatch.setenv("P42_OWNER_DATABASE_URL", upgraded.owner_database_url)
    monkeypatch.setenv("P42_ENVIRONMENT", "production")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("setting", "given", "problem"),
    [
        (installation.PUBLIC_URL, "p42.example.com", "like https://"),
        (installation.PUBLIC_URL, "https://p42.example.com/app", "nothing after the name"),
        (installation.PUBLIC_URL, "http://p42.example.com", "must start with https"),
        (installation.RELEASE_ADDRESS, "http://registry.example.com/x", "must start with https"),
        (installation.RELEASE_ADDRESS, "ftp://x", "like https://"),
        (installation.REQUESTS_PER_MINUTE, 5, "From 10 to 100000"),
        (installation.SESSION_IDLE, 2000, "From 5 to 1440"),
        (installation.SESSION_MAX, True, "From 1 to 720"),
    ],
)
def test_the_settings_are_checked_as_the_environment_was(
    production: None, setting: Setting, given: object, problem: str
) -> None:
    with pytest.raises(Invalid, match=problem):
        setting.check(given)


def test_good_values_are_kept_tidy(production: None) -> None:
    assert installation.PUBLIC_URL.check(" https://p42.example.com/ ") == "https://p42.example.com"
    assert installation.PUBLIC_URL.check("http://localhost:8080") == "http://localhost:8080"
    assert installation.PUBLIC_URL.check("") == ""
    assert installation.SESSION_IDLE.check(45) == 45


@pytest.fixture
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


async def _set(owner: AsyncEngine, name: str, value: str) -> None:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.execute(
                text("UPDATE sys.setting SET value = CAST(:v AS jsonb) WHERE name = :n"),
                {"v": value, "n": name},
            )


async def test_an_environment_value_becomes_the_setting_once(
    owner: AsyncEngine, production: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("P42_ENVIRONMENT", "qa")  # production refuses the tests' passwords
    module = f"t{uuid.uuid4().hex[:8]}"
    adopting = Setting(
        f"{module}.address", "Address", "", check=installation.PUBLIC_URL.check, adopt="public_url"
    )
    monkeypatch.setenv("P42_PUBLIC_URL", "https://from.env.example")
    get_settings.cache_clear()
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await write(conn, module, (adopting,))
            first = await conn.scalar(
                text("SELECT value FROM sys.setting WHERE name = :n"), {"n": adopting.name}
            )
    assert first == "https://from.env.example"
    # Changed in the app, then upgraded again: the app's value stays.
    await _set(owner, adopting.name, '"https://changed.example"')
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await write(conn, module, (adopting,))
            kept = await conn.scalar(
                text("SELECT value FROM sys.setting WHERE name = :n"), {"n": adopting.name}
            )
    assert kept == "https://changed.example"


async def test_processes_read_the_settings_while_running(
    owner: AsyncEngine, upgraded: Settings
) -> None:
    app = create_engine(upgraded.database_url)
    plain = upgraded.model_copy(update={"public_url": None, "session_idle_minutes": 30})
    try:
        await _set(owner, "sys.public_url", '"https://set.in.app"')
        await _set(owner, "sys.session_idle_minutes", "45")
        await installation.refresh(app)
        assert installation.public_url(plain) == "https://set.in.app"
        assert installation.session_idle(plain) == timedelta(minutes=45)
        # Empty in the app: the environment's value counts (development, tests).
        await _set(owner, "sys.public_url", '""')
        await installation.refresh(app)
        assert installation.public_url(plain) is None
        assert installation.public_url(upgraded) == upgraded.public_url
    finally:
        await _set(owner, "sys.session_idle_minutes", "30")
        await installation.refresh(app)
        await app.dispose()


async def test_turning_delivery_off_holds_within_the_next_read(
    owner: AsyncEngine, upgraded: Settings
) -> None:
    """Tests deliver (their environment says so); an administrator's setting wins once read."""
    app = create_engine(upgraded.database_url)
    try:
        await _set(owner, "sys.deliver_outside", "false")
        await installation.refresh(app)
        assert not outgoing.outside_allowed()
        await _set(owner, "sys.deliver_outside", "true")
        await installation.refresh(app)
        assert outgoing.outside_allowed()
    finally:
        await _set(owner, "sys.deliver_outside", "true")
        await installation.refresh(app)
        await app.dispose()
