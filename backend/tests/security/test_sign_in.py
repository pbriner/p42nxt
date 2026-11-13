"""Signing in: passwords, the second factor, sessions, slowing guessing down, the first admin."""

import base64
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer, SealError
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.sign_in import credentials, passwords, totp
from p42cloud.security.sign_in.credentials import NewKey
from p42cloud.security.sign_in.first_admin import create_first_admin
from p42cloud.security.sign_in.sessions import SignIn
from tests.security.helpers import acting

PASSWORD = "correct horse battery staple"


@dataclass
class Clock:
    moment: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __call__(self) -> datetime:
        return self.moment

    def code(self, key: bytes, steps_ahead: int = 0) -> str:
        return totp.code(key, totp.step_of(self.moment) + steps_ahead)


@dataclass
class Person:
    sys_id: uuid.UUID
    email: str
    key: bytes  # their authenticator app's key
    recovery: list[str]


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
def sealer() -> Sealer:
    return Sealer(os.urandom(32))


async def _person(engine: AsyncEngine, sealer: Sealer, *, second_factor: bool = True) -> Person:
    """Someone with a password and, unless told otherwise, an authenticator app."""
    email = f"p{uuid.uuid4().hex[:8]}@example.com"
    async with acting(engine, None) as records:
        person = (await records.create("sys.person", {"name": "Pat", "email": email})).sys_id
        await credentials.set_password(records.conn, person, PASSWORD)
        if not second_factor:
            return Person(person, email, b"", [])
        made = await credentials.start_totp(records.conn, sealer, person, "Phone")
        key = _key(made)
        codes = await credentials.confirm_totp(
            records.conn,
            sealer,
            person,
            made.credential,
            totp.code(key, totp.step_of(datetime.now(UTC)) - 1),
        )
    return Person(person, email, key, codes)


def _key(made: NewKey) -> bytes:
    padded = made.key + "=" * (-len(made.key) % 8)
    return base64.b32decode(padded)


def _sign_in(engine: AsyncEngine, sealer: Sealer, clock: Clock, upgraded: Settings) -> SignIn:
    return SignIn(engine, sealer, upgraded, now=clock)


@pytest.fixture
def anonymous() -> Generator[None]:
    with working_as(Context(channel="ui", access=EVERYTHING)):
        yield


# ---- the parts ----


def test_authenticator_codes_follow_the_standard() -> None:
    key = b"12345678901234567890"  # RFC 6238's test key; six digits of its codes
    assert totp.code(key, 59 // 30) == "287082"
    assert totp.code(key, 1111111109 // 30) == "081804"
    moment = datetime.fromtimestamp(1111111109, UTC)
    assert totp.accepted_step(key, "081804", moment, None) == 1111111109 // 30
    assert totp.accepted_step(key, "081804", moment, 1111111109 // 30) is None  # used already
    assert totp.link(key, "P42cloud", "a@b.c").startswith("otpauth://totp/P42cloud%3Aa%40b.c?")


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("short", "at least 12"),
        ("qwerty123456", "data breaches"),
        ("Q1W2E3R4T5Y6", "data breaches"),  # in any case
        ("annabelle-rules-2026", "email name"),
    ],
)
def test_weak_passwords_are_refused(password: str, problem: str) -> None:
    assert any(problem in p for p in passwords.problems(password, "annabelle@example.com"))


def test_passwords_are_hashed_with_argon2id() -> None:
    hashed = passwords.hash_password(PASSWORD)
    assert hashed.startswith("$argon2id$")
    assert passwords.verify(hashed, PASSWORD)
    assert not passwords.verify(hashed, PASSWORD + "!")
    assert not passwords.verify(None, PASSWORD)
    assert passwords.problems(PASSWORD) == []


def test_sealed_secrets_open_only_for_their_purpose() -> None:
    sealer = Sealer(os.urandom(32))
    sealed = sealer.seal(b"key", "second factor")
    assert sealer.unseal(sealed, "second factor") == b"key"
    with pytest.raises(SealError):
        sealer.unseal(sealed, "connection")
    with pytest.raises(SealError):
        sealer.unseal(sealed[:-4] + "AAAA", "second factor")
    with pytest.raises(SealError):
        Sealer(os.urandom(32)).unseal(sealed, "second factor")


# ---- signing in ----


async def test_password_then_second_factor_then_a_new_token(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    clock = Clock(datetime.now(UTC) + timedelta(minutes=2))  # past the code used to set it up
    sign_in = _sign_in(engine, sealer, clock, upgraded)
    with pytest.raises(Problem) as wrong:
        await sign_in.start(person.email, "not the password")
    waiting = await sign_in.start(person.email.upper(), PASSWORD, address="10.0.0.1")
    with pytest.raises(Problem):
        await sign_in.session(waiting.token)  # not signed in yet
    with pytest.raises(Problem) as bad_code:
        await sign_in.finish(waiting.token, "000000")
    done = await sign_in.finish(waiting.token, clock.code(person.key))
    with pytest.raises(Problem):
        await sign_in.finish(waiting.token, clock.code(person.key))  # the old token is spent
    session = await sign_in.session(done.token)
    assert (wrong.value.status, bad_code.value.status) == (401, 401)
    assert waiting.stage == "second_factor"
    assert done.stage == "active" and done.token != waiting.token
    assert session.person == person.sys_id and session.verified is not None


async def test_a_code_works_once_and_recovery_codes_too(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    clock = Clock(datetime.now(UTC) + timedelta(minutes=2))
    sign_in = _sign_in(engine, sealer, clock, upgraded)
    done = await sign_in.finish(
        (await sign_in.start(person.email, PASSWORD)).token, clock.code(person.key)
    )
    with pytest.raises(Problem):
        await sign_in.verify_again(done.token, clock.code(person.key))  # the same code again
    clock.moment += timedelta(seconds=30)
    await sign_in.verify_again(done.token, clock.code(person.key))
    recovery = person.recovery[0]
    await sign_in.finish((await sign_in.start(person.email, PASSWORD)).token, recovery.lower())
    with pytest.raises(Problem):
        await sign_in.finish((await sign_in.start(person.email, PASSWORD)).token, recovery)
    assert len(person.recovery) == credentials.RECOVERY_CODES


async def test_guessing_is_slowed_down_whether_the_account_exists_or_not(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    sign_in = _sign_in(engine, sealer, Clock(), upgraded)
    nobody = f"nobody{uuid.uuid4().hex[:6]}@example.com"
    for email in (person.email, nobody):
        for _ in range(5):
            with pytest.raises(Problem) as wrong:
                await sign_in.start(email, "guess guess guess")
            assert wrong.value.status == 401
        with pytest.raises(Problem) as slowed:
            await sign_in.start(email, PASSWORD)  # even the right password now waits
        assert slowed.value.status == 429
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            recorded = await conn.scalar(
                text(
                    "SELECT count(*) FROM sys.audit_log WHERE operation = 'sign_in_failed'"
                    " AND changes ->> 'email' = :e"
                ),
                {"e": nobody},
            )
    assert recorded == 6  # every failure is kept, though each attempt was refused


async def test_sessions_end_when_unused_and_at_their_latest(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    clock = Clock(datetime.now(UTC) + timedelta(minutes=2))
    sign_in = _sign_in(engine, sealer, clock, upgraded)

    async def signed_in() -> str:
        clock.moment += timedelta(seconds=30)
        waiting = await sign_in.start(person.email, PASSWORD)
        return (await sign_in.finish(waiting.token, clock.code(person.key))).token

    idle = await signed_in()
    clock.moment += timedelta(minutes=upgraded.session_idle_minutes + 1)
    with pytest.raises(Problem):
        await sign_in.session(idle)
    busy = await signed_in()
    for _ in range(upgraded.session_max_hours * 4):  # used every 15 minutes, never idle
        clock.moment += timedelta(minutes=15)
        try:
            await sign_in.session(busy)
        except Problem:
            break
    assert clock.moment - datetime.now(UTC) < timedelta(hours=upgraded.session_max_hours + 1)
    with pytest.raises(Problem):
        await sign_in.session(busy)


async def test_someone_without_a_second_factor_sets_one_up_first(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer, second_factor=False)
    clock = Clock()
    sign_in = _sign_in(engine, sealer, clock, upgraded)
    waiting = await sign_in.start(person.email, PASSWORD)
    with pytest.raises(Problem):
        await sign_in.finish(waiting.token, "123456")  # nothing to check against yet
    made = await sign_in.enroll(waiting.token, "Phone")
    done, codes = await sign_in.finish_enrolling(
        waiting.token, made.credential, clock.code(_key(made))
    )
    assert waiting.stage == "enroll"
    assert done.stage == "active"
    assert len(codes) == credentials.RECOVERY_CODES


async def test_a_new_password_ends_the_other_sessions(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    clock = Clock(datetime.now(UTC) + timedelta(minutes=2))
    sign_in = _sign_in(engine, sealer, clock, upgraded)
    done = await sign_in.finish(
        (await sign_in.start(person.email, PASSWORD)).token, clock.code(person.key)
    )
    async with acting(engine, None) as records:
        await credentials.set_password(records.conn, person.sys_id, "a whole new passphrase")
    with pytest.raises(Problem):
        await sign_in.session(done.token)
    with pytest.raises(Problem):
        await sign_in.start(person.email, PASSWORD)


# ---- what nobody sees, and who sees what ----


async def test_secrets_are_never_read_back_nor_logged(engine: AsyncEngine, sealer: Sealer) -> None:
    person = await _person(engine, sealer)
    async with acting(engine, None) as records:
        page = await records.list(
            "sys.credential", where={"field": "person", "op": "eq", "value": str(person.sys_id)}
        )
        logged = (
            (
                await records.conn.execute(
                    text(
                        "SELECT a.changes ->> 'secret' FROM sys.audit_log a"
                        " JOIN sys.credential c ON c.sys_id = a.record_id WHERE c.person = :p"
                        " AND jsonb_exists(a.changes, 'secret')"
                    ),
                    {"p": person.sys_id},
                )
            )
            .scalars()
            .all()
        )
    assert page.records and all("secret" not in r.values for r in page.records)
    assert logged and set(logged) == {"(changed)"}


async def test_people_see_and_end_their_own_sessions(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    other = await _person(engine, sealer)
    clock = Clock(datetime.now(UTC) + timedelta(minutes=2))
    sign_in = _sign_in(engine, sealer, clock, upgraded)
    mine = await sign_in.finish(
        (await sign_in.start(person.email, PASSWORD)).token, clock.code(person.key)
    )
    theirs = await sign_in.finish(
        (await sign_in.start(other.email, PASSWORD)).token, clock.code(other.key)
    )
    async with acting(engine, person.sys_id) as records:
        page = await records.list("sys.session", limit=500)
        assert {r.values["person"] for r in page.records} == {person.sys_id}
        assert all("token" not in r.values for r in page.records)
        active = next(r for r in page.records if r.values["stage"] == "active")
        await records.delete("sys.session", active.sys_id, active.etag)
    with pytest.raises(Problem):
        await sign_in.session(mine.token)
    assert (await sign_in.session(theirs.token)).person == other.sys_id


async def test_changing_access_needs_a_recent_second_factor(
    engine: AsyncEngine, sealer: Sealer
) -> None:
    async with acting(engine, None) as records:
        root = (await records.create("sys.person", {"name": "Root"})).sys_id
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": root})
    async with acting(engine, root) as records:
        stale = Context(
            channel="ui",
            person_id=root,
            access=records.access,
            verified_at=datetime.now(UTC) - timedelta(hours=1),
        )
    with working_as(stale), pytest.raises(Problem) as refused:
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            await records.create("sys.group", {"name": f"Late {uuid.uuid4().hex[:6]}"})
    assert refused.value.code == "verify_again"


# ---- the first administrator ----


async def test_the_first_admin_gets_a_password_and_an_app_at_once(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings
) -> None:
    async with acting(engine, None) as records:
        group = (
            await records.create("sys.group", {"name": f"First {uuid.uuid4().hex[:6]}"})
        ).sys_id
    email = f"first{uuid.uuid4().hex[:6]}@example.com"
    clock = Clock()

    async def app(made: NewKey) -> str:
        return clock.code(_key(made))

    codes = await create_first_admin(
        engine, sealer, name="First", email=email, password=PASSWORD, ask_code=app, group=group
    )
    with pytest.raises(Problem) as again:
        await create_first_admin(
            engine,
            sealer,
            name="Second",
            email="x" + email,
            password=PASSWORD,
            ask_code=app,
            group=group,
        )
    with working_as(Context(channel="ui", access=EVERYTHING)):
        waiting = await _sign_in(engine, sealer, clock, upgraded).start(email, PASSWORD)
    assert len(codes) == credentials.RECOVERY_CODES
    assert again.value.code == "admin_exists"
    assert waiting.stage == "second_factor"


async def test_a_wrong_code_makes_nothing(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings
) -> None:
    async with acting(engine, None) as records:
        group = (
            await records.create("sys.group", {"name": f"Empty {uuid.uuid4().hex[:6]}"})
        ).sys_id
    email = f"nope{uuid.uuid4().hex[:6]}@example.com"

    async def wrong(made: NewKey) -> str:
        return "000000"

    with pytest.raises(Problem) as refused:
        await create_first_admin(
            engine, sealer, name="Nope", email=email, password=PASSWORD, ask_code=wrong, group=group
        )
    async with acting(engine, None) as records:
        found = await records.list(
            "sys.person", where={"field": "email", "op": "eq", "value": email}
        )
    assert refused.value.code == "wrong_code"
    assert found.records == []


async def test_guessing_codes_in_a_session_is_slowed_down_too(
    engine: AsyncEngine, sealer: Sealer, upgraded: Settings, anonymous: None
) -> None:
    person = await _person(engine, sealer)
    clock = Clock(datetime.now(UTC) + timedelta(minutes=2))
    sign_in = _sign_in(engine, sealer, clock, upgraded)
    done = await sign_in.finish(
        (await sign_in.start(person.email, PASSWORD)).token, clock.code(person.key)
    )
    for _ in range(5):
        with pytest.raises(Problem):
            await sign_in.verify_again(done.token, "000000")
    clock.moment += timedelta(seconds=30)
    with pytest.raises(Problem) as slowed:
        await sign_in.verify_again(done.token, clock.code(person.key))
    assert slowed.value.status == 429


async def test_one_email_is_one_person(engine: AsyncEngine) -> None:
    email = f"Case{uuid.uuid4().hex[:6]}@Example.com"
    async with acting(engine, None) as records:
        made = await records.create("sys.person", {"name": "Case", "email": email})
    with pytest.raises(Problem) as twice:
        async with acting(engine, None) as records:
            await records.create("sys.person", {"name": "Case", "email": email.lower()})
    assert made.values["email"] == email.lower()
    assert [e.code for e in twice.value.errors] == ["not_unique"]
