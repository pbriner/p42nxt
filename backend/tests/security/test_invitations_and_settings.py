"""Settings as records, passwords off per kind, invitations and resets."""

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.sign_in import credentials
from p42cloud.security.sign_in.invitations import invite
from p42cloud.security.sign_in.sessions import SignIn
from tests.security.helpers import acting, change, context_of

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def anonymous() -> Generator[None]:
    with working_as(Context(channel="ui", access=EVERYTHING)):
        yield


async def _setting(engine: AsyncEngine, name: str) -> uuid.UUID:
    async with acting(engine, None) as records:
        page = await records.list("sys.setting", where={"field": "name", "op": "eq", "value": name})
    return page.records[0].sys_id


async def _set(engine: AsyncEngine, name: str, value: object) -> None:
    async with acting(engine, None) as records:
        await change(records, "sys.setting", await _setting(engine, name), {"value": value})


# ---- settings ----


async def test_settings_come_with_their_module_and_their_checks(engine: AsyncEngine) -> None:
    zone = await _setting(engine, "sys.time_zone")
    async with acting(engine, None) as records:
        found = await records.read("sys.setting", zone)
        for values, code in (
            ({"value": "Mars/Olympus"}, "validation"),
            ({"name": "sys.other"}, "declared_only"),
        ):
            with pytest.raises(Problem) as refused:
                await records.update("sys.setting", zone, values, found.etag)
            assert refused.value.code == code
        with pytest.raises(Problem) as made:
            await records.create("sys.setting", {"name": "sys.mine", "label": "Mine"})
    assert found.values["value"] == "UTC"
    assert made.value.code == "declared_only"


async def test_everyone_reads_the_public_settings_only(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": "Reader"})
    async with acting(engine, person.sys_id) as records:
        page = await records.list("sys.setting", limit=500)
    names = {r.values["name"] for r in page.records}
    # Not sys.passwords_off_for.
    assert names == {
        "sys.time_zone",
        "sys.stage",
        "sys.theme",
        "sys.app_name",
        "sys.language",
        "sys.main_currency",
    }


async def test_the_time_zone_reaches_the_filters(engine: AsyncEngine) -> None:
    with working_as(Context(channel="ui", access=EVERYTHING, zone="Pacific/Kiritimati")):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
    assert records.situation.zone == ZoneInfo("Pacific/Kiritimati")


async def test_passwords_can_be_switched_off_per_kind(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        staff = await records.create("sys.person", {"name": "Staff", "email": f"s{tag}@x.test"})
        admin = await records.create(
            "sys.person",
            {"name": "Admin", "email": f"a{tag}@x.test", "emergency_access": True},
        )
        for person in (staff, admin):
            await credentials.set_password(records.conn, person.sys_id, PASSWORD)
    sign_in = SignIn(engine, Sealer(os.urandom(32)), upgraded)
    await _set(engine, "sys.passwords_off_for", ["employee"])
    try:
        with pytest.raises(Problem) as off:
            await sign_in.start(f"s{tag}@x.test", PASSWORD)
        with pytest.raises(Problem) as wrong:
            await sign_in.start(f"s{tag}@x.test", "not the password")  # says nothing more
        assert (await sign_in.start(f"a{tag}@x.test", PASSWORD)).stage == "enroll"
    finally:
        await _set(engine, "sys.passwords_off_for", [])
    assert off.value.code == "passwords_off"
    assert wrong.value.code == "not_signed_in"


# ---- invitations and resets ----


async def _admin(engine: AsyncEngine) -> uuid.UUID:
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": "Admin"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": person.sys_id})
    return person.sys_id


async def _invite(
    engine: AsyncEngine, by: uuid.UUID, person: uuid.UUID, *, reset: bool = False
) -> str:
    async with acting(engine, by) as records:
        return (await invite(records.conn, person, reset=reset)).token


async def test_an_invitation_leads_to_a_password_then_a_second_factor(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    admin = await _admin(engine)
    async with acting(engine, None) as records:
        newcomer = await records.create(
            "sys.person", {"name": "New", "email": f"n{uuid.uuid4().hex[:6]}@x.test"}
        )
    token = await _invite(engine, admin, newcomer.sys_id)
    sign_in = SignIn(engine, Sealer(os.urandom(32)), upgraded)
    with pytest.raises(Problem) as weak:
        await sign_in.accept_invitation(token, "short")
    started = await sign_in.accept_invitation(token, PASSWORD)
    with pytest.raises(Problem) as again:
        await sign_in.accept_invitation(token, PASSWORD)
    assert weak.value.code == "weak_password"  # and the link still worked afterwards
    assert started.stage == "enroll"
    assert again.value.code == "invitation"


async def test_a_reset_ends_sessions_and_credentials(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    admin = await _admin(engine)
    email = f"r{uuid.uuid4().hex[:6]}@x.test"
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": "Reset", "email": email})
        await credentials.set_password(records.conn, person.sys_id, PASSWORD)
    sign_in = SignIn(engine, Sealer(os.urandom(32)), upgraded)
    waiting = await sign_in.start(email, PASSWORD)
    token = await _invite(engine, admin, person.sys_id, reset=True)
    with pytest.raises(Problem):
        await sign_in.start(email, PASSWORD)  # the old password is gone
    async with acting(engine, None) as records:
        open_sessions = await records.conn.scalar(
            text("SELECT count(*) FROM sys.session WHERE person = :p AND deleted IS NULL"),
            {"p": person.sys_id},
        )
    assert open_sessions == 0 and waiting.token
    assert (await sign_in.accept_invitation(token, "a brand new passphrase")).stage == "enroll"


async def test_only_administrators_invite_and_only_people(engine: AsyncEngine) -> None:
    admin = await _admin(engine)
    async with acting(engine, None) as records:
        plain = (await records.create("sys.person", {"name": "Plain"})).sys_id
        robot = (await records.create("sys.person", {"name": "Robot", "kind": "service"})).sys_id
    for by, person, code in ((plain, plain, "admins_only"), (admin, robot, "service_account")):
        with pytest.raises(Problem) as refused:
            await _invite(engine, by, person)
        assert refused.value.code == code
    context = await context_of(engine, admin)
    stale = Context(
        channel="ui",
        person_id=admin,
        access=context.access,
        verified_at=datetime.now(UTC) - timedelta(hours=1),
    )
    with working_as(stale), pytest.raises(Problem) as old:
        async with transaction(engine, "sys") as conn:
            await invite(conn, plain)
    assert old.value.code == "verify_again"


async def test_an_expired_link_is_refused(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    admin = await _admin(engine)
    async with acting(engine, None) as records:
        person = (await records.create("sys.person", {"name": "Late"})).sys_id
    token = await _invite(engine, admin, person)
    later = SignIn(
        engine, Sealer(os.urandom(32)), upgraded, now=lambda: datetime.now(UTC) + timedelta(days=8)
    )
    with pytest.raises(Problem) as refused:
        await later.accept_invitation(token, PASSWORD)
    assert refused.value.code == "invitation"
