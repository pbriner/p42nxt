"""References filled from a key: the database links a record to the one its key names."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.metadata import version

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.reference import TYPE, Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from tests.security.helpers import acting

ITEM = Table("item", "Item", {"name": String("Name", max_length=40, unique=True)})


def _module(module_id: str, version_: str, note: Table) -> Module:
    return Module(
        id=module_id,
        publisher="test",
        name="Links",
        version=version_,
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(ITEM, note),
    )


def _note(module_id: str, *, linked: bool) -> Table:
    fields = {"item": String("Item name", max_length=40), "text": String("Text", max_length=40)}
    if linked:
        fields["item_ref"] = Reference("Item", f"{module_id}.item", key="item")
        fields["table_ref"] = Reference("Table", "sys.table", key="text", match="schema.name")
    return Table("note", "Note", fields)


@pytest.fixture(scope="module")
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


async def _sql(owner: AsyncEngine, statement: str, **params: object) -> list[dict[str, object]]:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            found = await conn.execute(text(statement), params)
            return [dict(r) for r in found.mappings()] if found.returns_rows else []


async def test_a_key_links_its_record_on_every_write(
    owner: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    plain = _note(module_id, linked=False)
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", plain)])
    items, notes = f"{module_id}.item", f"{module_id}.note"
    await _sql(owner, f"INSERT INTO {items} (name) VALUES ('a'), ('b')")
    await _sql(owner, f"INSERT INTO {notes} (item, text) VALUES ('a', 'sys.job')")

    # The link added later: the records there already get it on the upgrade.
    linked = _note(module_id, linked=True)
    with working_as(Context(channel="test")):
        done = await install_modules(owner, [CORE, _module(module_id, "1.1.0", linked)])
    assert any("Link the records of" in d for d in done)
    ids = {r["name"]: r["sys_id"] for r in await _sql(owner, f"SELECT name, sys_id FROM {items}")}
    job_table = (
        await _sql(owner, "SELECT sys_id FROM sys.table WHERE schema = 'sys' AND name = 'job'")
    )[0]["sys_id"]

    def linked_to() -> str:
        return f"SELECT item_ref, table_ref FROM {notes} ORDER BY created"

    assert await _sql(owner, linked_to()) == [{"item_ref": ids["a"], "table_ref": job_table}]

    # On a change of the key, and whatever a writer sends as the link itself.
    await _sql(owner, f"UPDATE {notes} SET item = 'b', item_ref = :wrong", wrong=ids["a"])
    assert (await _sql(owner, linked_to()))[0]["item_ref"] == ids["b"]
    # A key naming nothing links nothing.
    await _sql(owner, f"UPDATE {notes} SET item = 'nobody', text = NULL")
    assert await _sql(owner, linked_to()) == [{"item_ref": None, "table_ref": None}]
    # A new record, written by plain SQL outside the app, too.
    await _sql(owner, f"INSERT INTO {notes} (item) VALUES ('a')")
    assert (await _sql(owner, linked_to()))[-1]["item_ref"] == ids["a"]

    # Upgrading again changes nothing.
    with working_as(Context(channel="test")):
        again = await install_modules(owner, [CORE, _module(module_id, "1.1.0", linked)])
    assert not any("Link" in d for d in again)


def test_a_key_must_be_a_field() -> None:
    assert TYPE.option_problems({"table": "sys.job", "key": "Bad Name", "match": "name"})
    assert TYPE.option_problems({"table": "sys.job", "match": "name"})  # a match without a key
    assert not TYPE.option_problems({"table": "sys.table", "key": "t", "match": "schema.name"})


async def test_a_picked_link_fills_in_its_name(
    owner: AsyncEngine, upgraded: Settings, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    linked = _note(module_id, linked=True)
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", linked)])
    items, notes = f"{module_id}.item", f"{module_id}.note"
    await _sql(owner, f"INSERT INTO {items} (name) VALUES ('a'), ('b')")
    ids = {r["name"]: r["sys_id"] for r in await _sql(owner, f"SELECT name, sys_id FROM {items}")}
    # Only the link given, as a form sends it: the name comes from the record picked.
    await _sql(owner, f"INSERT INTO {notes} (item_ref) VALUES (:a)", a=ids["a"])
    assert await _sql(owner, f"SELECT item, item_ref FROM {notes}") == [
        {"item": "a", "item_ref": ids["a"]}
    ]
    # Another record picked, the name left as it was: the name follows.
    await _sql(owner, f"UPDATE {notes} SET item_ref = :b", b=ids["b"])
    assert await _sql(owner, f"SELECT item FROM {notes}") == [{"item": "b"}]


async def test_a_required_name_is_satisfied_by_its_picked_link(
    upgraded: Settings, new_module: Callable[[], Awaitable[str]], owner: AsyncEngine
) -> None:
    module_id = await new_module()
    note = Table(
        "note",
        "Note",
        {
            "item": String("Item name", max_length=40, required=True),
            "item_ref": Reference("Item", f"{module_id}.item", key="item"),
        },
    )
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", note)])
    app = create_engine(upgraded.database_url)
    async with acting(app, None) as records:
        item = await records.create(f"{module_id}.item", {"name": "picked"})
        made = await records.create(f"{module_id}.note", {"item_ref": item.sys_id})
        with pytest.raises(Problem) as refused:  # neither: still required
            await records.create(f"{module_id}.note", {})
    await app.dispose()
    assert made.values["item"] == "picked"
    assert [e.field for e in refused.value.errors] == ["item"]


async def test_a_field_can_become_read_only_on_an_upgrade(
    owner: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    before = Table("note", "Note", {"seen": DateTime("Seen")})
    after = Table("note", "Note", {"seen": DateTime("Seen", read_only=True)})
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", before)])
        done = await install_modules(owner, [CORE, _module(module_id, "1.1.0", after)])
    assert f"{module_id} 1.0.0 → 1.1.0" in done  # how forms show it: no change of its values


async def test_a_link_to_any_record_can_name_its_table_field_on_an_upgrade(
    owner: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    fields = {"about_table": String("About table", max_length=80)}
    before = Table("note", "Note", fields | {"about": AnyRecord("About")})
    after = Table("note", "Note", fields | {"about": AnyRecord("About", table_field="about_table")})
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", before)])
        done = await install_modules(owner, [CORE, _module(module_id, "1.1.0", after)])
    assert f"{module_id} 1.0.0 → 1.1.0" in done
