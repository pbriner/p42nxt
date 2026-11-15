"""Signing in through a provider: what's trusted, what's checked, who it lets in."""

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, switch_role, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.providers.service import Providers, set_client_secret
from p42cloud.security.sign_in import credentials
from p42cloud.security.sign_in.sessions import SignIn
from tests.security.fake_provider import FakeProvider
from tests.security.helpers import acting

PASSWORD = "correct horse battery staple"


@dataclass
class World:
    engine: AsyncEngine
    idp: FakeProvider
    providers: Providers
    sign_in: SignIn
    provider: uuid.UUID
    domain: str

    async def sign_in_as(self, claims: dict[str, Any]) -> Any:
        redirect = await self.providers.begin(self.provider)
        state, code = self.idp.authorize(redirect.url, claims)
        return await self.providers.complete(state, code, address="10.1.1.1")


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


async def _world(engine: AsyncEngine, upgraded: Settings, **provider: Any) -> World:
    idp = FakeProvider()
    sealer = Sealer(os.urandom(32))
    settings = upgraded.model_copy(update={"public_url": "https://p42.test"})
    domain = f"corp{uuid.uuid4().hex[:6]}.test"
    async with acting(engine, None) as records:
        made = await records.create(
            "sys.identity_provider",
            {
                "name": f"Corp {uuid.uuid4().hex[:6]}",
                "issuer": idp.issuer,
                "client_id": idp.client_id,
                "kind": "employee",
                "domains": [domain],
                "claims": {"name": "name"},
            }
            | provider,
        )
        await set_client_secret(records.conn, sealer, made.sys_id, idp.client_secret)
    http = httpx.AsyncClient(transport=idp.transport())
    sign_in = SignIn(engine, sealer, settings)
    providers = Providers(engine, sealer, settings=settings, http=http, sign_in=sign_in)
    return World(engine, idp, providers, sign_in, made.sys_id, domain)


@pytest.fixture
def anonymous() -> Generator[None]:
    with working_as(Context(channel="ui", access=EVERYTHING)):
        yield


def _claims(world: World, **extra: Any) -> dict[str, Any]:
    tag = uuid.uuid4().hex[:6]
    return {
        "sub": f"sub-{tag}",
        "email": f"pat.{tag}@{world.domain}",
        "email_verified": True,
        "name": f"Pat {tag}",
    } | extra


async def _person_by_email(engine: AsyncEngine, email: str) -> dict[str, Any] | None:
    async with acting(engine, None) as records:
        page = await records.list(
            "sys.person", where={"field": "email", "op": "eq", "value": email}
        )
    return page.records[0].values if page.records else None


async def test_a_provider_may_make_people_and_signs_them_in(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded, create_people=True)
    claims = _claims(world)
    first = await world.sign_in_as(claims)
    person = await _person_by_email(engine, claims["email"])
    renamed = await world.sign_in_as(claims | {"name": "Pat Renamed"})
    session = await world.sign_in.session(first.token)
    assert first.stage == "active"  # the provider's own second factor counts
    assert person is not None and person["kind"] == "employee"
    assert renamed.person == first.person == session.person == person["sys_id"]
    assert (await _person_by_email(engine, claims["email"]) or {})["name"] == "Pat Renamed"


async def test_without_making_people_only_known_ones_get_in(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded)
    stranger = _claims(world)
    with pytest.raises(Problem) as refused:
        await world.sign_in_as(stranger)
    assert refused.value.status == 401
    assert await _person_by_email(engine, stranger["email"]) is None

    known = _claims(world)
    async with acting(engine, None) as records:
        await records.create("sys.person", {"name": "Known", "email": known["email"]})
    done = await world.sign_in_as(known)
    # Linked once: later the subject ID finds them, even with another email.
    again = await world.sign_in_as(known | {"email": "changed@elsewhere.test"})
    assert done.person == again.person


async def test_an_unconfirmed_email_never_links(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded)
    claims = _claims(world, email_verified=False)
    async with acting(engine, None) as records:
        await records.create("sys.person", {"name": "Victim", "email": claims["email"]})
    with pytest.raises(Problem):
        await world.sign_in_as(claims)
    trusting = await _world(engine, upgraded, trust_email=True)  # a company's own tenant
    claims = _claims(trusting, email_verified=None)
    async with acting(engine, None) as records:
        await records.create("sys.person", {"name": "Staff", "email": claims["email"]})
    assert (await trusting.sign_in_as(claims)).stage == "active"


async def test_a_provider_signs_in_only_its_kind_of_person(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded, kind="external", domains=None)
    claims = _claims(world)
    async with acting(engine, None) as records:
        await records.create("sys.person", {"name": "Employee", "email": claims["email"]})
    with pytest.raises(Problem):
        await world.sign_in_as(claims)  # an employee isn't linked to a customers' provider


@pytest.mark.parametrize(
    "tamper",
    [
        {"key": rsa.generate_private_key(public_exponent=65537, key_size=2048)},  # not its key
        {
            "alg": "HS256",
            "key": "a shared secret long enough for HS256!",
        },  # shared-secret algorithm
        {"iss": "https://elsewhere.test"},
        {"aud": "someone-else"},
        {"exp": 1000, "iat": 900},  # long expired
        {"nonce": "not-ours"},
    ],
)
async def test_bad_id_tokens_are_refused(
    engine: AsyncEngine, upgraded: Settings, anonymous: None, tamper: dict[str, Any]
) -> None:
    world = await _world(engine, upgraded, create_people=True)
    world.idp.tamper = tamper
    claims = _claims(world)
    with pytest.raises(Problem) as refused:
        await world.sign_in_as(claims)
    assert refused.value.status == 401
    assert await _person_by_email(engine, claims["email"]) is None
    async with transaction(engine, "sys") as conn:
        reason = await conn.scalar(
            text(
                "SELECT changes ->> 'reason' FROM sys.audit_log"
                " WHERE operation = 'provider_sign_in_failed' ORDER BY sys_id DESC LIMIT 1"
            )
        )
    assert reason  # the reason is kept for whoever looks into it


async def test_a_state_works_once_and_not_for_long(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded, create_people=True)
    claims = _claims(world)
    redirect = await world.providers.begin(world.provider)
    state, code = world.idp.authorize(redirect.url, claims)
    await world.providers.complete(state, code)
    with pytest.raises(Problem):
        await world.providers.complete(state, code)
    late = await world.providers.begin(world.provider)
    state, code = world.idp.authorize(late.url, _claims(world))
    world.providers.now = lambda: datetime.now(UTC) + timedelta(minutes=11)
    with pytest.raises(Problem):
        await world.providers.complete(state, code)


async def test_inactive_people_are_refused(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded, create_people=True)
    claims = _claims(world)
    done = await world.sign_in_as(claims)
    async with acting(engine, None) as records:
        found = await records.read("sys.person", done.person)
        await records.update("sys.person", done.person, {"active": False}, found.etag)
    with pytest.raises(Problem):
        await world.sign_in_as(claims)


async def test_a_claimed_domain_signs_in_through_its_provider(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded)
    staff, admin = f"staff@{world.domain}", f"admin@{world.domain}"
    async with acting(engine, None) as records:
        for email, emergency in ((staff, False), (admin, True)):
            made = await records.create(
                "sys.person", {"name": email, "email": email, "emergency_access": emergency}
            )
            await credentials.set_password(records.conn, made.sys_id, PASSWORD)
    for email, password in (
        (staff, PASSWORD),
        (admin, "wrong password!"),
        (f"x@{world.domain}", PASSWORD),
    ):
        with pytest.raises(Problem) as refused:
            await world.sign_in.start(email, password)
        assert refused.value.code == "use_provider"  # the same answer, whatever the account
    # Emergency access keeps the password (and then the second factor), so an outage of the
    # provider can't lock everyone out.
    assert (await world.sign_in.start(admin, PASSWORD)).stage == "enroll"


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"issuer": "http://idp.test"}, "issuer"),
        ({"client_secret": "plain"}, "client_secret"),
        ({"claims": {"kind": "role"}}, "claims"),
        ({"claims": {"emergency_access": "x"}}, "claims"),
        ({"claims": {"email": "upn"}}, "claims"),
        ({"domains": ["Not A Domain"]}, "domains"),
    ],
)
async def test_unsafe_provider_settings_are_refused(
    engine: AsyncEngine, values: dict[str, Any], field: str
) -> None:
    with pytest.raises(Problem) as refused:
        async with acting(engine, None) as records:
            await records.create(
                "sys.identity_provider",
                {
                    "name": f"Bad {uuid.uuid4().hex[:6]}",
                    "issuer": "https://idp.test",
                    "client_id": "x",
                }
                | values,
            )
    assert [e.field for e in refused.value.errors] == [field]


async def test_the_client_secret_is_sealed(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded)
    async with transaction(engine, "sys") as conn:
        await switch_role(conn, "sys")
        stored = await conn.scalar(
            text("SELECT client_secret FROM sys.identity_provider WHERE sys_id = :id"),
            {"id": world.provider},
        )
    assert stored and world.idp.client_secret not in stored and stored.startswith("v1:")


async def test_providers_need_the_installations_address(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    settings = upgraded.model_copy(update={"public_url": None})
    sealer = Sealer(os.urandom(32))
    providers = Providers(
        engine,
        sealer,
        settings=settings,
        http=httpx.AsyncClient(),
        sign_in=SignIn(engine, sealer, settings),
    )
    with pytest.raises(Problem) as off:  # asked when used: the address is a setting
        _ = providers.redirect_uri
    assert off.value.code == "providers_off"


async def test_a_provider_must_keep_to_https(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded, create_people=True)
    original = world.idp.transport()

    def plain_keys(request: httpx.Request) -> httpx.Response:
        answer = original.handle_request(request)
        if request.url.path.endswith("openid-configuration"):
            config = answer.json() | {"jwks_uri": "http://169.254.169.254/keys"}
            return httpx.Response(200, json=config)
        return answer

    world.providers.http = httpx.AsyncClient(transport=httpx.MockTransport(plain_keys))
    with pytest.raises(Problem) as refused:
        await world.providers.begin(world.provider)
    assert refused.value.code == "provider_unreachable"


async def test_a_group_follows_the_providers_groups_claim(
    engine: AsyncEngine, upgraded: Settings, anonymous: None
) -> None:
    world = await _world(engine, upgraded, create_people=True, groups_claim="groups")
    async with acting(engine, None) as records:
        group = await records.create(
            "sys.group",
            {
                "name": f"Staff {uuid.uuid4().hex[:6]}",
                "provider": world.provider,
                "provider_group": "staff",
            },
        )
    claims = _claims(world)
    joined = await world.sign_in_as(claims | {"groups": ["staff", "other"]})
    async with acting(engine, None) as records:
        member = await records.list(
            "sys.membership", where={"field": "group", "op": "eq", "value": str(group.sys_id)}
        )
    assert [r.values["person"] for r in member.records] == [joined.person]
    await world.sign_in_as(claims | {"groups": ["other"]})
    async with acting(engine, None) as records:
        after = await records.list(
            "sys.membership", where={"field": "group", "op": "eq", "value": str(group.sys_id)}
        )
    assert after.records == []  # left the provider's group: left ours at the next sign-in


async def test_a_providers_group_is_never_powerful_nor_kept_by_hand(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    world = await _world(engine, upgraded)
    async with acting(engine, None) as records:
        group = await records.create(
            "sys.group",
            {
                "name": f"Synced {uuid.uuid4().hex[:6]}",
                "provider": world.provider,
                "provider_group": "x",
            },
        )
        admin_role = await records.conn.scalar(
            text("SELECT sys_id FROM sys.role WHERE name = 'sys.admin'")
        )
        person = await records.create("sys.person", {"name": "Someone"})
        with pytest.raises(Problem) as powerful:
            await records.create("sys.group_role", {"group": group.sys_id, "role": admin_role})
        with pytest.raises(Problem) as by_hand:
            await records.create("sys.membership", {"group": group.sys_id, "person": person.sys_id})
        with pytest.raises(Problem) as both:
            await records.create(
                "sys.group",
                {
                    "name": f"Both {uuid.uuid4().hex[:6]}",
                    "provider": world.provider,
                    "rule": {"field": "kind", "op": "eq", "value": "employee"},
                },
            )
    assert powerful.value.code == "provider_group"  # even for the system itself
    assert by_hand.value.code == "from_rule"
    assert both.value.code == "validation"
