"""Passkeys as a second factor: registered once, then a challenge signed for this site only."""

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.sign_in import credentials
from p42cloud.security.sign_in.sessions import SignIn, Started
from tests.security.authenticator import SoftKey
from tests.security.helpers import acting

PASSWORD = "correct horse battery staple"
SITE, ORIGIN = "p42.test", "https://p42.test"


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def sign_in(engine: AsyncEngine, upgraded: Settings) -> Generator[SignIn]:
    settings = upgraded.model_copy(update={"public_url": ORIGIN})
    with working_as(Context(channel="ui", access=EVERYTHING)):
        yield SignIn(engine, Sealer(os.urandom(32)), settings)


async def _person(engine: AsyncEngine) -> str:
    """Someone with a password and no second factor yet; returns their email."""
    email = f"k{uuid.uuid4().hex[:8]}@example.com"
    async with acting(engine, None) as records:
        made = await records.create("sys.person", {"name": "Kim", "email": email})
        await credentials.set_password(records.conn, made.sys_id, PASSWORD)
    return email


async def _with_passkey(sign_in: SignIn, engine: AsyncEngine) -> tuple[str, SoftKey, list[str]]:
    email = await _person(engine)
    key = SoftKey(SITE, ORIGIN)
    waiting = await sign_in.start(email, PASSWORD)
    options = await sign_in.passkey_registration(waiting.token)
    _, codes = await sign_in.enroll_with_passkey(waiting.token, key.register(options), "Laptop")
    return email, key, codes


async def _signed_in(sign_in: SignIn, email: str, key: SoftKey) -> Started:
    waiting = await sign_in.start(email, PASSWORD)
    options = await sign_in.passkey_challenge(waiting.token)
    assert key.offered(options)
    return await sign_in.finish(waiting.token, key.sign(options))


async def test_a_passkey_can_be_the_first_second_factor(
    sign_in: SignIn, engine: AsyncEngine
) -> None:
    email, key, codes = await _with_passkey(sign_in, engine)
    done = await _signed_in(sign_in, email, key)
    assert done.stage == "active"
    assert len(codes) == credentials.RECOVERY_CODES
    assert (await sign_in.start(email, PASSWORD)).stage == "second_factor"


async def test_a_challenge_works_once(sign_in: SignIn, engine: AsyncEngine) -> None:
    email, key, _ = await _with_passkey(sign_in, engine)
    waiting = await sign_in.start(email, PASSWORD)
    options = await sign_in.passkey_challenge(waiting.token)
    answer = key.sign(options)
    with pytest.raises(Problem):
        await sign_in.finish(waiting.token, key.sign({"challenge": "AAAA"}))  # not the one asked
    with pytest.raises(Problem):
        await sign_in.finish(waiting.token, answer)  # the challenge was used up by that attempt


@pytest.mark.parametrize(
    "wrong",
    [
        {"origin": "https://p42.test.evil.example"},  # a look-alike site
        {"rp_id": "evil.example"},
    ],
)
async def test_answers_for_another_site_are_refused(
    sign_in: SignIn, engine: AsyncEngine, wrong: dict[str, str]
) -> None:
    email, key, _ = await _with_passkey(sign_in, engine)
    waiting = await sign_in.start(email, PASSWORD)
    options = await sign_in.passkey_challenge(waiting.token)
    with pytest.raises(Problem) as refused:
        await sign_in.finish(waiting.token, key.sign(options, **wrong))
    assert refused.value.status == 401


async def test_a_copied_passkey_is_refused(sign_in: SignIn, engine: AsyncEngine) -> None:
    email, key, _ = await _with_passkey(sign_in, engine)
    await _signed_in(sign_in, email, key)
    copy = SoftKey(SITE, ORIGIN, key=key.key, key_id=key.key_id, counter=0)  # an old copy
    waiting = await sign_in.start(email, PASSWORD)
    with pytest.raises(Problem):
        await sign_in.finish(
            waiting.token, copy.sign(await sign_in.passkey_challenge(waiting.token))
        )
    # The same key with its counter going on is fine: only the counter told them apart.
    assert (await _signed_in(sign_in, email, key)).stage == "active"


async def test_someone_elses_passkey_is_refused(sign_in: SignIn, engine: AsyncEngine) -> None:
    email, _, _ = await _with_passkey(sign_in, engine)
    _, stranger, _ = await _with_passkey(sign_in, engine)
    waiting = await sign_in.start(email, PASSWORD)
    options = await sign_in.passkey_challenge(waiting.token)
    assert not stranger.offered(options)
    with pytest.raises(Problem):
        await sign_in.finish(waiting.token, stranger.sign(options))


async def test_a_passkey_added_while_signed_in_ends_the_other_sessions(
    sign_in: SignIn, engine: AsyncEngine
) -> None:
    email, key, _ = await _with_passkey(sign_in, engine)
    here = await _signed_in(sign_in, email, key)
    elsewhere = await _signed_in(sign_in, email, key)
    second = SoftKey(SITE, ORIGIN)
    options = await sign_in.passkey_registration(here.token)
    assert len(options["excludeCredentials"]) == 1  # the browser won't add the same key twice
    codes = await sign_in.add_passkey(here.token, second.register(options), "Security key")
    assert codes == []  # the person has recovery codes already
    with pytest.raises(Problem):
        await sign_in.session(elsewhere.token)
    verified = await sign_in.verify_again(
        here.token, second.sign(await sign_in.passkey_challenge(here.token))
    )
    assert verified.verified is not None
    assert verified.verified <= datetime.now(UTC)


async def test_without_the_address_passkeys_are_off(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    email = await _person(engine)
    with working_as(Context(channel="ui", access=EVERYTHING)):
        sign_in = SignIn(
            engine, Sealer(os.urandom(32)), upgraded.model_copy(update={"public_url": None})
        )
        waiting = await sign_in.start(email, PASSWORD)
        with pytest.raises(Problem) as off:
            await sign_in.passkey_registration(waiting.token)
    assert off.value.code == "passkeys_off"


def test_the_address_must_be_https_in_production() -> None:
    def settings(public_url: str) -> Settings:
        return Settings(
            environment="production",
            database_url="postgresql+asyncpg://p42_app:x@h/p42",
            owner_database_url="postgresql+asyncpg://p42_owner:y@h/p42",
            public_url=public_url,
        )

    for bad in ("http://p42.example.com", "https://p42.example.com/path", "ftp://x"):
        with pytest.raises(ValueError, match="public_url"):
            settings(bad)
    assert settings("http://localhost:8000").public_url


async def test_adding_a_passkey_needs_a_recent_second_factor(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    settings = upgraded.model_copy(update={"public_url": ORIGIN})
    clock = [datetime.now(UTC)]
    with working_as(Context(channel="ui", access=EVERYTHING)):
        sign_in = SignIn(engine, Sealer(os.urandom(32)), settings, now=lambda: clock[0])
        email, key, _ = await _with_passkey(sign_in, engine)
        here = await _signed_in(sign_in, email, key)
        clock[0] += timedelta(minutes=20)
        await sign_in.session(here.token)  # still signed in (used within the idle time) ...
        clock[0] += timedelta(minutes=20)
        await sign_in.session(here.token)
        options = await sign_in.passkey_registration(here.token)
        with pytest.raises(Problem) as refused:
            await sign_in.add_passkey(here.token, SoftKey(SITE, ORIGIN).register(options), "Mine")
    assert refused.value.code == "verify_again"  # ... but the second factor is 40 minutes old
