"""Access: groups hold roles, roles hold grants, and the records service applies them in SQL.

A shop where everyone may browse names and prices, owners see and change everything of their own
items, and a team sees everything of its team's items. Fields count per record: seeing one's own
notes never opens other people's notes.
"""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.metadata import declare
from p42cloud.data.records import Record, Records
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.undo import undo
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.access.roles import Grant, Role, write_roles
from tests.security.helpers import acting, context_of

MINE = {"field": "owner", "op": "eq", "value": "@me"}
MY_TEAM = {"field": "team", "op": "in", "value": "@my_groups"}


def _roles(m: str) -> list[Role]:
    item = f"{m}.item"
    return [
        Role(f"{m}.browse", "Browse", (Grant(item, ("read",), fields=("name", "price")),)),
        Role(
            f"{m}.own",
            "Own items",
            (
                Grant(item, ("read", "write", "delete"), condition=MINE),
                Grant(item, ("create",), condition=MINE, fields=("name", "price", "owner")),
            ),
        ),
        Role(f"{m}.team", "Team items", (Grant(item, ("read",), condition=MY_TEAM),)),
        Role(f"{m}.everything", "Everything", (Grant(item, ("read", "write")),)),
        Role(f"{m}.boss", "Boss", includes=(f"{m}.browse",)),
        # Refers to a field that is retired later: must then grant nothing.
        Role(
            f"{m}.fragile",
            "Fragile",
            (Grant(item, ("read",), condition={"field": "price", "op": "gt", "value": 0}),),
        ),
    ]


@dataclass
class Shop:
    module: str
    engine: AsyncEngine
    people: dict[str, uuid.UUID]
    items: dict[str, uuid.UUID]
    groups: dict[str, uuid.UUID]

    @property
    def item(self) -> str:
        return f"{self.module}.item"


@pytest.fixture(scope="module")
async def shop(
    new_module: Callable[[], Awaitable[str]], upgraded: Settings
) -> AsyncGenerator[Shop]:
    m = await new_module()
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="install")):
        async with owner_transaction(owner) as conn:
            await declare(
                conn,
                m,
                [
                    Table(
                        "item",
                        "Item",
                        {
                            "name": String("Name", max_length=40, required=True),
                            "price": Decimal("Price", precision=10, scale=2),
                            "note": Text("Note"),
                            "owner": Reference("Owner", "sys.person"),
                            "team": Reference("Team", "sys.group"),
                        },
                    )
                ],
            )
            await write_roles(conn, await load_catalog(conn), m, _roles(m))
    await owner.dispose()
    engine = create_engine(upgraded.database_url)
    people: dict[str, uuid.UUID] = {}
    groups: dict[str, uuid.UUID] = {}
    items: dict[str, uuid.UUID] = {}
    async with acting(engine, None) as records:
        for name in ("Anna", "Bob", "Cleo", "Dave", "Eve", "Fred"):
            made = await records.create("sys.person", {"name": f"{name} {m}"})
            people[name] = made.sys_id
        await records.update(
            "sys.person",
            people["Cleo"],
            {"active": False},
            (await records.read("sys.person", people["Cleo"])).etag,
        )
        plan = {
            "Staff": (["Anna", "Bob", "Cleo"], ["browse"], None),
            "Sales": (["Anna"], ["team"], None),
            "Owners": (["Anna", "Bob"], ["own"], None),
            "Bargains": (["Bob"], ["everything"], {"field": "price", "op": "lt", "value": 10}),
            "Viewers": (["Eve"], ["team"], None),
            "Bosses": (["Fred"], ["boss", "fragile"], None),
        }
        for group, (members, roles, narrowing) in plan.items():
            made = await records.create("sys.group", {"name": f"{group} {m}"})
            groups[group] = made.sys_id
            for member in members:
                await records.create(
                    "sys.membership", {"group": made.sys_id, "person": people[member]}
                )
            for role in roles:
                role_id = await records.conn.scalar(
                    text("SELECT sys_id FROM sys.role WHERE name = :n"), {"n": f"{m}.{role}"}
                )
                await records.create(
                    "sys.group_role",
                    {"group": made.sys_id, "role": role_id, "condition": narrowing},
                )
        for key, values in {
            "A1": {
                "name": "Anna desk",
                "price": 100,
                "note": "anna secret",
                "owner": "Anna",
                "team": "Sales",
            },
            "B1": {"name": "Bob lamp", "price": 5, "note": "bob secret", "owner": "Bob"},
            "B2": {
                "name": "Bob chair",
                "price": 50,
                "note": "bob team note",
                "owner": "Bob",
                "team": "Sales",
            },
            "X1": {"name": "Cheap pen", "price": 2, "note": "pen note"},
        }.items():
            resolved: dict[str, Any] = dict(values)
            if "owner" in resolved:
                resolved["owner"] = people[str(values["owner"])]
            if "team" in resolved:
                resolved["team"] = groups[str(values["team"])]
            items[key] = (await records.create(f"{m}.item", resolved)).sys_id
    yield Shop(m, engine, people, items, groups)
    await engine.dispose()


def _by_key(shop: Shop, found: list[Record]) -> dict[str, dict[str, Any]]:
    keys = {v: k for k, v in shop.items.items()}
    return {keys[r.sys_id]: r.values for r in found if r.sys_id in keys}


# ---- who sees what ----


@pytest.mark.parametrize(
    ("person", "notes"),
    [
        # Own items and the team's items, never other people's notes.
        ("Anna", {"A1", "B2"}),
        # Own items, and everything about cheap items through the narrowed role.
        ("Bob", {"B1", "B2", "X1"}),
        # Browsing through an included role, and a grant on every priced item.
        ("Fred", {"A1", "B1", "B2", "X1"}),
    ],
)
async def test_fields_count_per_record(shop: Shop, person: str, notes: set[str]) -> None:
    async with acting(shop.engine, shop.people[person]) as records:
        page = await records.list(shop.item, limit=500)
    seen = _by_key(shop, page.records)
    assert set(seen) == {"A1", "B1", "B2", "X1"}
    assert {k for k, v in seen.items() if "note" in v} == notes
    assert all("name" in v and "price" in v for v in seen.values())


@pytest.mark.parametrize("person", ["Cleo", "Dave"])
async def test_no_groups_or_inactive_means_no_access(shop: Shop, person: str) -> None:
    async with acting(shop.engine, shop.people[person]) as records:
        with pytest.raises(Problem) as refused:
            await records.list(shop.item)
    assert refused.value.status == 403


async def test_records_out_of_reach_are_not_found(shop: Shop) -> None:
    async with acting(shop.engine, shop.people["Eve"]) as records:
        page = await records.list(shop.item)
        with pytest.raises(Problem) as missing:
            await records.read(shop.item, shop.items["A1"])
    assert page.records == []
    assert missing.value.status == 404


async def test_hidden_fields_can_not_be_found_by_filtering(shop: Shop) -> None:
    async with acting(shop.engine, shop.people["Anna"]) as records:
        bob = await records.list(
            shop.item, where={"field": "note", "op": "contains", "value": "bob"}
        )
        others = await records.list(
            shop.item, where={"not": {"field": "note", "op": "contains", "value": "bob"}}
        )
        by_note = await records.list(shop.item, sort=["note"], limit=500)
    # Bob's lamp has "bob secret" too, but Anna may not read that note.
    assert set(_by_key(shop, bob.records)) == {"B2"}
    assert set(_by_key(shop, others.records)) == {"A1", "B1", "X1"}
    ordered = [k for k in _by_key(shop, by_note.records)]
    assert ordered[:2] == ["A1", "B2"]  # readable notes first, the hidden ones sort as empty


async def test_following_a_reference_stays_within_what_may_be_read(shop: Shop) -> None:
    bob = {"field": "owner.name", "op": "eq", "value": f"bob {shop.module}"}
    by_email = {"field": "owner.email", "op": "empty"}
    async with acting(shop.engine, shop.people["Anna"]) as records:
        named = await records.list(shop.item, where=bob)
        hidden = await records.list(shop.item, where=by_email)
    # Everyone may read people's names, so Bob's items are found by his name, but only where
    # Anna may read the owner: on his chair (her team's), not on his lamp (names and prices only).
    assert set(_by_key(shop, named.records)) == {"B2"}
    # ... but not other people's emails: only her own (empty) one finds anything, her own desk.
    assert set(_by_key(shop, hidden.records)) == {"A1"}


# ---- who may change what ----


async def test_changes_need_a_grant_for_the_record_and_each_field(shop: Shop) -> None:
    async with acting(shop.engine, shop.people["Anna"]) as records:
        mine = await records.read(shop.item, shop.items["A1"])
        changed = await records.update(shop.item, mine.sys_id, {"note": "anna's new"}, mine.etag)
        bobs = await records.read(shop.item, shop.items["B2"])
        with pytest.raises(Problem) as refused:
            await records.update(shop.item, bobs.sys_id, {"name": "Mine now"}, bobs.etag)
    assert changed.values["note"] == "anna's new"
    assert refused.value.status == 403


async def test_create_only_what_the_grant_allows(shop: Shop) -> None:
    anna = shop.people["Anna"]
    async with acting(shop.engine, anna) as records:
        made = await records.create(shop.item, {"name": "Anna shelf", "owner": anna})
        for values in (
            {"name": "For Bob", "owner": shop.people["Bob"]},  # not her own
            {"name": "Noted", "owner": anna, "note": "x"},  # a field the grant doesn't cover
        ):
            with pytest.raises(Problem) as refused:
                await records.create(shop.item, values)
            assert refused.value.status == 403
        found = await records.list(
            shop.item, where={"field": "name", "op": "in", "value": ["for bob", "noted"]}
        )
    assert made.values["owner"] == anna
    assert found.records == []  # the refused ones were never made


async def test_delete_and_restore_need_their_own_grants(shop: Shop) -> None:
    anna = shop.people["Anna"]
    async with acting(shop.engine, anna) as records:
        made = await records.create(shop.item, {"name": "Anna box", "owner": anna})
        bobs = await records.read(shop.item, shop.items["B2"])
        with pytest.raises(Problem) as not_hers:
            await records.delete(shop.item, bobs.sys_id, bobs.etag)
        deleted = await records.delete(shop.item, made.sys_id, made.etag)
        with pytest.raises(Problem) as no_restore:
            await records.restore(shop.item, deleted.sys_id)
    assert not_hers.value.status == 403
    assert no_restore.value.status == 403


async def test_undo_needs_the_rights_of_today(shop: Shop) -> None:
    context = await context_of(shop.engine, shop.people["Bob"])
    with working_as(context):
        async with transaction(shop.engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            lamp = await records.read(shop.item, shop.items["B1"])
            await records.update(shop.item, lamp.sys_id, {"price": "6"}, lamp.etag)
    async with acting(shop.engine, shop.people["Anna"]) as records:
        result = await undo(records.conn, records.catalog, request=context.request_id)
    assert result.changed == []
    assert [s.reason for s in result.skipped] == [
        "You may not write this record (or these fields)."
    ]


# ---- roles and the core's own access ----


async def test_the_administrators_group_holds_the_core_roles(shop: Shop) -> None:
    async with acting(shop.engine, None) as records:
        roles = await records.list(
            "sys.group_role", where={"field": "group", "op": "eq", "value": str(ADMINISTRATORS)}
        )
        names: set[str] = set()
        for r in roles.records:
            names.add((await records.read("sys.role", r.values["role"])).values["name"])
        admin = await records.create("sys.person", {"name": "Admin"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin.sys_id})
    assert names == {"sys.admin", "sys.access_admin"}
    async with acting(shop.engine, admin.sys_id) as records:
        people = await records.list("sys.person", limit=500)
        with pytest.raises(Problem) as refused:
            await records.list(shop.item)
    assert len(people.records) >= len(shop.people)
    assert refused.value.status == 403  # the platform's admin isn't the shop's


@pytest.mark.parametrize(
    ("role", "code"),
    [
        (Role("{m}.x", "X", (Grant("nowhere.thing", ("read",)),)), "unknown_table"),
        (Role("{m}.x", "X", (Grant("{m}.item", ("steal",)),)), "bad_operation"),
        (Role("{m}.x", "X", (Grant("{m}.item", ("read",), fields=("colour",)),)), "unknown_field"),
        (
            Role("{m}.x", "X", (Grant("{m}.item", ("read",), condition={"field": "x"}),)),
            "bad_shape",
        ),
        (Role("other.x", "X"), "bad_name"),
        (Role("{m}.x", "X", includes=("{m}.x",)), "cycle"),
    ],
)
async def test_roles_are_checked_before_they_are_written(
    shop: Shop, upgraded: Settings, role: Role, code: str
) -> None:
    m = shop.module
    fixed = Role(
        role.name.replace("{m}", m),
        role.label,
        tuple(
            Grant(g.table.replace("{m}", m), g.operations, g.condition, g.fields)
            for g in role.grants
        ),
        tuple(i.replace("{m}", m) for i in role.includes),
    )
    owner = create_engine(upgraded.owner_database_url)
    try:
        with working_as(Context(channel="install")), pytest.raises(Problem) as refused:
            async with owner_transaction(owner) as conn:
                await write_roles(conn, await load_catalog(conn), m, [*_roles(m), fixed])
    finally:
        await owner.dispose()
    assert code in [e.code for e in refused.value.errors]


async def test_a_grant_that_breaks_grants_nothing(shop: Shop, upgraded: Settings) -> None:
    """Fred's fragile role filters on the price; once the price is retired it gives nothing."""
    m = shop.module
    owner = create_engine(upgraded.owner_database_url)

    async def item_with(*names: str) -> None:
        fields = {
            "name": String("Name", max_length=40, required=True),
            "price": Decimal("Price", precision=10, scale=2),
            "note": Text("Note"),
            "owner": Reference("Owner", "sys.person"),
            "team": Reference("Team", "sys.group"),
        }
        table = Table("item", "Item", {n: f for n, f in fields.items() if n in names})
        with working_as(Context(channel="install")):
            async with owner_transaction(owner) as conn:
                await declare(conn, m, [table])

    await item_with("name", "note", "owner", "team")
    try:
        async with acting(shop.engine, shop.people["Fred"]) as records:
            page = await records.list(shop.item, limit=500)
    finally:
        await item_with("name", "price", "note", "owner", "team")
        await owner.dispose()
    seen = _by_key(shop, page.records)
    # Browsing still shows the names; the broken grant opens no notes.
    assert set(seen) == {"A1", "B1", "B2", "X1"}
    assert all("note" not in v for v in seen.values())
