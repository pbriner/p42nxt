"""Record numbers: the database gives each record the next one, never twice, never changed."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table, check
from p42cloud.data.presenting import describe
from p42cloud.data.types.number import Number
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from tests.security.helpers import acting, change


def _module(module_id: str, version_: str, *, numbered: bool) -> Module:
    fields: dict[str, Any] = {"text": String("Text", max_length=40)}
    if numbered:
        fields["number"] = Number("Invoice", prefix="INV", digits=3)
        fields["plain"] = Number("Plain", digits=1)
    return Module(
        id=module_id,
        publisher="test",
        name="Numbers",
        version=version_,
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(Table("invoice", "Invoice", fields),),
    )


@pytest.fixture(scope="module")
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
async def app(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


async def _sql(owner: AsyncEngine, statement: str, **params: object) -> list[dict[str, object]]:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            found = await conn.execute(text(statement), params)
            return [dict(r) for r in found.mappings()] if found.returns_rows else []


async def _refused(app: AsyncEngine, work: Callable[..., Awaitable[Any]]) -> list[str]:
    with pytest.raises(Problem) as refused:
        async with acting(app, None) as records:
            await work(records)
    return [e.code for e in refused.value.errors]


async def test_records_are_numbered_in_order_and_numbers_never_come_back(
    owner: AsyncEngine, app: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    table = f"{module_id}.invoice"
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", numbered=False)])
    await _sql(owner, f"INSERT INTO {table} (text) VALUES ('first')")
    await _sql(owner, f"INSERT INTO {table} (text) VALUES ('second')")

    # Added later: the records there already are numbered in the order they were made.
    with working_as(Context(channel="test")):
        done = await install_modules(owner, [CORE, _module(module_id, "1.1.0", numbered=True)])
    assert any("Number the records of" in d for d in done)
    numbered = await _sql(owner, f"SELECT text, number, plain FROM {table} ORDER BY created")
    assert numbered == [
        {"text": "first", "number": "INV-001", "plain": "1"},
        {"text": "second", "number": "INV-002", "plain": "2"},
    ]

    async with acting(app, None) as records:
        made = await records.create(table, {"text": "third"})
        # Read only for everyone, also in the description forms read.
        field = next(
            f
            for f in describe(records.catalog.tables[table], records.access, "en")["fields"]
            if f["name"] == "number"
        )
    assert made.values["number"] == "INV-003"
    assert (field["create"], field["write"]) == (False, False)

    async def typed(records: Any) -> None:
        await records.create(table, {"text": "typed", "number": "INV-999"})

    async def changed(records: Any) -> None:
        await change(records, table, made.sys_id, {"number": "INV-777"})

    assert await _refused(app, typed) == ["read_only"]
    assert await _refused(app, changed) == ["read_only"]
    # Not even plain SQL changes one.
    with pytest.raises(Exception, match="never changes"):
        await _sql(owner, f"UPDATE {table} SET number = 'INV-500' WHERE text = 'third'")

    # A deleted record keeps its number, and the next record doesn't get it again.
    async with acting(app, None) as records:
        current = await records.read(table, made.sys_id)
        await records.delete(table, made.sys_id, current.etag)
    async with acting(app, None) as records:
        after = await records.create(table, {"text": "fourth"})
    assert after.values["number"] == "INV-004"

    # Upgrading again changes nothing.
    with working_as(Context(channel="test")):
        again = await install_modules(owner, [CORE, _module(module_id, "1.1.0", numbered=True)])
    assert not [d for d in again if "Number the records" in d or "numbering" in d], again


async def test_a_number_outgrows_its_digits_instead_of_being_cut(
    owner: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    table = f"{module_id}.invoice"
    with working_as(Context(channel="test")):
        await install_modules(owner, [CORE, _module(module_id, "1.0.0", numbered=True)])
    for n in range(12):
        await _sql(owner, f"INSERT INTO {table} (text) VALUES ('{n}')")
    found = await _sql(owner, f"SELECT plain FROM {table} ORDER BY created DESC LIMIT 3")
    assert [r["plain"] for r in found] == ["12", "11", "10"]


@pytest.mark.parametrize(
    ("options", "problem"),
    [
        ({"prefix": "inv"}, "prefix is up to"),
        ({"prefix": "TOOLONGPREFIX"}, "prefix is up to"),
        ({"digits": 0}, "digits must be"),
        ({"digits": 13}, "digits must be"),
    ],
)
def test_a_number_field_checks_its_options(options: dict[str, Any], problem: str) -> None:
    table = Table("t", "T", {"n": Number("N", **options)})
    with pytest.raises(Problem) as refused:
        check("zz", [table])
    assert any(problem in e.message for e in refused.value.errors)
