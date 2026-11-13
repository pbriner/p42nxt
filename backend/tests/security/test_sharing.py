"""Personal records: only the owner and those they share with see them; no role overrides it."""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.metadata import declare
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.access.roles import Grant, Role, write_roles
from tests.security.helpers import acting, change, role_id


@dataclass
class Wallet:
    engine: AsyncEngine
    budget: str
    anna: uuid.UUID
    bob: uuid.UUID
    root: uuid.UUID
    tables: uuid.UUID  # sys.table record of the budget table
    plain: uuid.UUID  # sys.table record of a table that isn't personal


@pytest.fixture(scope="module")
async def wallet(
    new_module: Callable[[], Awaitable[str]], upgraded: Settings
) -> AsyncGenerator[Wallet]:
    m = await new_module()
    owner = create_engine(upgraded.owner_database_url)
    budget = Table(
        "budget",
        "Budget",
        {
            "name": String("Name", max_length=40, required=True),
            "amount": Decimal("Amount", precision=12, scale=2),
            "owner": Reference("Owner", "sys.person", required=True),
        },
        owner="owner",
    )
    note = Table("note", "Note", {"text": String("Text", max_length=40)})
    with working_as(Context(channel="install")):
        async with owner_transaction(owner) as conn:
            await declare(conn, m, [budget, note])
            everything = ("read", "create", "write", "delete", "restore")
            await write_roles(
                conn,
                await load_catalog(conn),
                m,
                [Role(f"{m}.user", "User", (Grant(f"{m}.budget", everything),))],
            )
    await owner.dispose()
    engine = create_engine(upgraded.database_url)
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        anna, bob, root = [
            (await records.create("sys.person", {"name": f"{n} {tag}"})).sys_id
            for n in ("Anna", "Bob", "Root")
        ]
        users = await records.create("sys.group", {"name": f"Wallet {tag}"})
        await records.create(
            "sys.group_role", {"group": users.sys_id, "role": await role_id(records, f"{m}.user")}
        )
        for person in (anna, bob, root):
            await records.create("sys.membership", {"group": users.sys_id, "person": person})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": root})
        tables = await records.list("sys.table", where={"field": "schema", "op": "eq", "value": m})
        ids = {r.values["name"]: r.sys_id for r in tables.records}
    yield Wallet(engine, f"{m}.budget", anna, bob, root, ids["budget"], ids["note"])
    await engine.dispose()


async def _share(w: Wallet, person: uuid.UUID, record: uuid.UUID, **extra: object) -> None:
    async with acting(w.engine, w.anna) as records:
        await records.create(
            "sys.share", {"table": w.tables, "record": record, "person": person, **extra}
        )


async def test_only_the_owner_sees_a_personal_record(wallet: Wallet) -> None:
    async with acting(wallet.engine, wallet.anna) as records:
        mine = await records.create(wallet.budget, {"name": "Holidays", "owner": wallet.anna})
        with pytest.raises(Problem) as for_bob:
            await records.create(wallet.budget, {"name": "For Bob", "owner": wallet.bob})
    for person in (wallet.bob, wallet.root):  # the administrator too: no role overrides it
        async with acting(wallet.engine, person) as records:
            page = await records.list(wallet.budget)
            with pytest.raises(Problem) as missing:
                await records.read(wallet.budget, mine.sys_id)
        assert mine.sys_id not in {r.sys_id for r in page.records}
        assert missing.value.status == 404
    assert for_bob.value.status == 403


async def test_a_share_lets_someone_read_or_change_it(wallet: Wallet) -> None:
    async with acting(wallet.engine, wallet.anna) as records:
        made = await records.create(wallet.budget, {"name": "Car", "owner": wallet.anna})
    await _share(wallet, wallet.bob, made.sys_id)
    async with acting(wallet.engine, wallet.bob) as records:
        seen = await records.read(wallet.budget, made.sys_id)
        with pytest.raises(Problem) as read_only:
            await change(records, wallet.budget, made.sys_id, {"amount": 10})
    assert seen.values["name"] == "Car"
    assert read_only.value.status == 403

    async with acting(wallet.engine, wallet.anna) as records:
        shares = await records.list(
            "sys.share", where={"field": "record", "op": "eq", "value": str(made.sys_id)}
        )
        await change(records, "sys.share", shares.records[0].sys_id, {"edit": True})
    async with acting(wallet.engine, wallet.bob) as records:
        changed = await change(records, wallet.budget, made.sys_id, {"amount": 10})
    assert str(changed.values["amount"]) == "10.00"


async def test_a_share_ends_at_its_time(wallet: Wallet) -> None:
    async with acting(wallet.engine, wallet.anna) as records:
        made = await records.create(wallet.budget, {"name": "Old", "owner": wallet.anna})
    await _share(wallet, wallet.bob, made.sys_id, until=datetime.now(UTC) - timedelta(minutes=1))
    async with acting(wallet.engine, wallet.bob) as records:
        with pytest.raises(Problem) as missing:
            await records.read(wallet.budget, made.sys_id)
    assert missing.value.status == 404


async def test_only_the_owner_shares(wallet: Wallet) -> None:
    async with acting(wallet.engine, wallet.anna) as records:
        made = await records.create(wallet.budget, {"name": "Rent", "owner": wallet.anna})
    await _share(wallet, wallet.bob, made.sys_id, edit=True)
    with pytest.raises(Problem) as not_owner:
        async with acting(wallet.engine, wallet.bob) as records:
            await records.create(
                "sys.share", {"table": wallet.tables, "record": made.sys_id, "person": wallet.root}
            )
    with pytest.raises(Problem) as not_personal:
        await _share(wallet, wallet.bob, uuid.uuid7(), table=wallet.plain)
    assert not_owner.value.code == "not_owner"
    assert not_personal.value.code == "not_personal"


async def test_people_see_their_own_shares_only(wallet: Wallet) -> None:
    async with acting(wallet.engine, wallet.anna) as records:
        made = await records.create(wallet.budget, {"name": "Gifts", "owner": wallet.anna})
    await _share(wallet, wallet.bob, made.sys_id)
    for person, sees in ((wallet.anna, True), (wallet.bob, True), (wallet.root, False)):
        async with acting(wallet.engine, person) as records:
            page = await records.list(
                "sys.share", where={"field": "record", "op": "eq", "value": str(made.sys_id)}
            )
        assert bool(page.records) is sees


async def test_a_share_never_hands_the_record_over(wallet: Wallet) -> None:
    async with acting(wallet.engine, wallet.anna) as records:
        made = await records.create(wallet.budget, {"name": "Savings", "owner": wallet.anna})
    await _share(wallet, wallet.bob, made.sys_id, edit=True)
    with pytest.raises(Problem) as refused:
        async with acting(wallet.engine, wallet.bob) as records:
            await change(records, wallet.budget, made.sys_id, {"owner": wallet.bob})
    assert refused.value.status == 403
    assert [e.field for e in refused.value.errors] == ["owner"]
