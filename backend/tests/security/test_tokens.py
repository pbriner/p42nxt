"""API tokens: acting as one account, with at most its rights, only the roles named."""

import os
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.metadata import declare
from p42cloud.data.records import Records
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, switch_role, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.access.roles import Grant, Role, write_roles
from p42cloud.security.sign_in import credentials
from p42cloud.security.sign_in.sessions import SignIn
from p42cloud.security.tokens.service import TokenUse, make_token, use_token
from tests.security.helpers import acting, change, context_of, role_id


@dataclass
class Shop:
    engine: AsyncEngine
    item: str
    anna: uuid.UUID
    bob: uuid.UUID
    robot: uuid.UUID
    root: uuid.UUID
    reader: str
    editor: str


@pytest.fixture(scope="module")
async def shop(
    new_module: Callable[[], Awaitable[str]], upgraded: Settings
) -> AsyncGenerator[Shop]:
    m = await new_module()
    item = f"{m}.item"
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="install")):
        async with owner_transaction(owner) as conn:
            await declare(conn, m, [Table("item", "Item", {"name": String("Name", max_length=40)})])
            await write_roles(
                conn,
                await load_catalog(conn),
                m,
                [
                    Role(f"{m}.reader", "Reader", (Grant(item, ("read",)),)),
                    Role(f"{m}.editor", "Editor", (Grant(item, ("create", "write")),)),
                ],
            )
    await owner.dispose()
    engine = create_engine(upgraded.database_url)
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        anna, bob, root = [
            (await records.create("sys.person", {"name": f"{n} {tag}"})).sys_id
            for n in ("Anna", "Bob", "Root")
        ]
        robot = (
            await records.create("sys.person", {"name": f"Robot {tag}", "kind": "service"})
        ).sys_id
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": root})
        for name, members, roles in (
            ("Shop", (anna,), ("reader", "editor")),
            ("Robots", (robot,), ("reader",)),
        ):
            group = (await records.create("sys.group", {"name": f"{name} {tag}"})).sys_id
            for role in roles:
                await records.create(
                    "sys.group_role",
                    {"group": group, "role": await role_id(records, f"{m}.{role}")},
                )
            for member in members:
                await records.create("sys.membership", {"group": group, "person": member})
        await records.create(item, {"name": "Seed"})
    yield Shop(engine, item, anna, bob, robot, root, f"{m}.reader", f"{m}.editor")
    await engine.dispose()


async def _make(shop: Shop, by: uuid.UUID, person: uuid.UUID, **options: object) -> str:
    async with acting(shop.engine, by) as records:
        made = await make_token(records.conn, person, "Script", **options)  # type: ignore[arg-type]
    return made.token


@asynccontextmanager
async def _with(shop: Shop, use: TokenUse) -> AsyncGenerator[Records]:
    """A request made with the token, as the API will make it."""
    with working_as(Context(channel=use.channel, person_id=use.person, access=use.access)):
        async with transaction(shop.engine, "sys") as conn:
            yield Records(conn, await load_catalog(conn))


@pytest.fixture
def anonymous() -> object:
    with working_as(Context(channel="api", access=EVERYTHING)):
        yield


async def test_a_token_acts_as_its_account(shop: Shop, anonymous: object) -> None:
    token = await _make(shop, shop.anna, shop.anna)
    use = await use_token(shop.engine, token, address="10.0.0.9")
    async with _with(shop, use) as records:
        made = await records.create(shop.item, {"name": "By script"})
        page = await records.list(shop.item)
        await switch_role(records.conn, "sys")
        stored = await records.conn.scalar(
            text("SELECT count(*) FROM sys.api_token WHERE token = :t"), {"t": token}
        )
    assert token.startswith("p42_")
    assert use.person == shop.anna and use.channel == "api:token:Script"
    assert made.values["source"] == "api:token:Script"
    assert page.records
    assert stored == 0  # only the hash is kept


async def test_a_token_gets_only_the_roles_it_names(shop: Shop, anonymous: object) -> None:
    reading = await use_token(
        shop.engine, await _make(shop, shop.anna, shop.anna, roles=[shop.reader])
    )
    async with _with(shop, reading) as records:
        assert (await records.list(shop.item)).records
        with pytest.raises(Problem) as refused:
            await records.create(shop.item, {"name": "Nope"})
    assert refused.value.status == 403


async def test_a_token_never_gets_more_than_its_account(shop: Shop, anonymous: object) -> None:
    # The robot holds only the reader role; naming the editor role gives it nothing.
    robot = await use_token(
        shop.engine, await _make(shop, shop.root, shop.robot, roles=[shop.editor])
    )
    async with _with(shop, robot) as records:
        with pytest.raises(Problem) as refused:
            await records.list(shop.item)
    assert refused.value.status == 403


async def test_a_token_can_not_change_access(shop: Shop, anonymous: object) -> None:
    root = await use_token(shop.engine, await _make(shop, shop.root, shop.root))
    async with _with(shop, root) as records:
        with pytest.raises(Problem) as refused:
            await records.create("sys.group", {"name": f"By token {uuid.uuid4().hex[:6]}"})
    assert refused.value.code == "verify_again"  # a token gives no second factor


async def test_who_may_make_tokens_for_whom(shop: Shop) -> None:
    for by, person, code in (
        (shop.anna, shop.bob, "not_yours"),  # people only for themselves
        (shop.anna, shop.robot, "admins_only"),  # service accounts: administrators
    ):
        with pytest.raises(Problem) as refused:
            await _make(shop, by, person)
        assert refused.value.code == code
    context = await context_of(shop.engine, shop.anna)
    stale = Context(
        channel="ui",
        person_id=shop.anna,
        access=context.access,
        verified_at=datetime.now(UTC) - timedelta(hours=1),
    )
    with working_as(stale), pytest.raises(Problem) as old:
        async with transaction(shop.engine, "sys") as conn:
            await make_token(conn, shop.anna, "Late")
    assert old.value.code == "verify_again"


@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({"expires": datetime.now(UTC) + timedelta(days=400)}, "expires"),
        ({"expires": datetime.now(UTC) - timedelta(days=1)}, "expires"),
        ({"roles": ["no.such_role"]}, "roles"),
    ],
)
async def test_tokens_are_checked_when_made(
    shop: Shop, options: dict[str, object], field: str
) -> None:
    with pytest.raises(Problem) as refused:
        await _make(shop, shop.anna, shop.anna, **options)
    assert [e.field for e in refused.value.errors] == [field]


async def test_a_token_stops_working(shop: Shop, anonymous: object) -> None:
    soon = await _make(shop, shop.anna, shop.anna, expires=datetime.now(UTC) + timedelta(seconds=1))
    revoked = await _make(shop, shop.anna, shop.anna)
    async with acting(shop.engine, shop.anna) as records:
        mine = await records.list("sys.api_token", limit=500)
        assert all("token" not in r.values for r in mine.records)
        made = next(r for r in mine.records if revoked.startswith(r.values["prefix"]))
        await records.delete("sys.api_token", made.sys_id, made.etag)
    with pytest.raises(Problem):
        await use_token(shop.engine, revoked)
    await use_token(shop.engine, soon)  # still valid for a moment ...
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(shop.engine, "sys") as conn:
            await conn.execute(
                text(
                    "UPDATE sys.api_token SET expires = now() - interval '1 second'"
                    " WHERE prefix = :p"
                ),
                {"p": soon[:12]},
            )
    with pytest.raises(Problem):
        await use_token(shop.engine, soon)  # ... then not


async def test_an_inactive_account_loses_its_tokens(shop: Shop, anonymous: object) -> None:
    async with acting(shop.engine, None) as records:
        temp = (await records.create("sys.person", {"name": "Temp", "kind": "service"})).sys_id
    token = await _make(shop, shop.root, temp)
    await use_token(shop.engine, token)
    async with acting(shop.engine, None) as records:
        await change(records, "sys.person", temp, {"active": False})
    with pytest.raises(Problem) as refused:
        await use_token(shop.engine, token)
    assert refused.value.status == 401


async def test_guessing_tokens_is_slowed_down(shop: Shop, anonymous: object) -> None:
    address = f"10.9.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}"
    for _ in range(20):
        with pytest.raises(Problem) as wrong:
            await use_token(shop.engine, "p42_" + uuid.uuid4().hex, address=address)
        assert wrong.value.status == 401
    with pytest.raises(Problem) as slowed:
        await use_token(shop.engine, await _make(shop, shop.anna, shop.anna), address=address)
    assert slowed.value.status == 429


async def test_service_accounts_never_sign_in_with_a_password(
    shop: Shop, upgraded: Settings, anonymous: object
) -> None:
    email = f"robot{uuid.uuid4().hex[:6]}@example.com"
    async with acting(shop.engine, None) as records:
        robot = (
            await records.create("sys.person", {"name": "Bot", "email": email, "kind": "service"})
        ).sys_id
        await credentials.set_password(records.conn, robot, "correct horse battery staple")
    sign_in = SignIn(shop.engine, Sealer(os.urandom(32)), upgraded)
    with pytest.raises(Problem) as refused:
        await sign_in.start(email, "correct horse battery staple")
    assert refused.value.status == 401
