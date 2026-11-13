"""Tables built from records: declared, written into the metadata, built and changed by the
engine, and the catalog every process caches."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import MetadataCache, load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.metadata import declare
from p42cloud.data.schema import apply, plan, read_database
from p42cloud.data.types import Field
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import owner_transaction, transaction
from p42cloud.kernel.errors import Problem

Declare = Callable[[str, list[Table]], Awaitable[list[str]]]


@pytest.fixture
async def module(new_module: Callable[[], Awaitable[str]]) -> str:
    return await new_module()


@pytest.fixture
async def build(owner_engine: AsyncEngine) -> AsyncIterator[Declare]:
    async def run(schema: str, tables: list[Table]) -> list[str]:
        with working_as(Context(channel="install")):
            async with owner_transaction(owner_engine) as conn:
                return await declare(conn, schema, tables)

    yield run


def _category() -> Table:
    return Table("category", "Category", {"name": String("Name", max_length=40, required=True)})


def _tables(schema: str, **changes: Field | None) -> list[Table]:
    """A category and a product; `changes` replace product fields, None removes one."""
    fields: dict[str, Field | None] = {
        "name": String("Name", max_length=40, required=True, unique=True),
        "price": Decimal("Price", precision=10, scale=2),
        "state": Choice("State", {"draft": "Draft", "sold": "Sold"}),
        "category": Reference("Category", f"{schema}.category"),
        **changes,
    }
    product = Table("product", "Product", {k: v for k, v in fields.items() if v is not None})
    return [_category(), product]


async def _run(app_engine: AsyncEngine, schema: str, sql: str, **values: Any) -> Any:
    with working_as(Context(channel="ui")):
        async with transaction(app_engine, schema) as conn:
            result = await conn.execute(text(sql), values)
            return result.first() if result.returns_rows else None


async def test_the_metadata_tables_describe_themselves(owner_engine: AsyncEngine) -> None:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner_engine) as conn:
            catalog = await load_catalog(conn)
            database = await read_database(conn, {t.schema for t in catalog.tables.values()})
    assert {"sys.table", "sys.field", "sys.field_type"} <= set(catalog.tables)
    field = catalog.tables["sys.field"]
    assert [f.name for f in field.fields][:3] == ["table", "name", "type"]
    assert field.fields[0].field.options == {"table": "sys.table"}
    assert plan(catalog, database) == []


async def test_a_declared_table_is_built_and_works(
    module: str, build: Declare, app_engine: AsyncEngine
) -> None:
    done = await build(module, _tables(module))
    assert f"Create the table {module}.product" in done
    category = await _run(
        app_engine,
        module,
        f"INSERT INTO {module}.category (name) VALUES ('Desks') RETURNING sys_id",
    )
    row = await _run(
        app_engine,
        module,
        f"INSERT INTO {module}.product (name, price, state, category)"
        " VALUES ('Oak desk', 199.5, 'draft', :c) RETURNING price, created_by",
        c=category[0],
    )
    assert str(row[0]) == "199.50"


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        ("(price)", "(1)"),  # name is required
        ("(name, state)", "('Lamp', 'broken')"),  # not a choice
        ("(name, category)", "('Lamp', '01900000-0000-7000-8000-000000000009')"),  # no such record
    ],
)
async def test_the_database_refuses_what_the_declaration_forbids(
    module: str, build: Declare, app_engine: AsyncEngine, columns: str, values: str
) -> None:
    await build(module, _tables(module))
    with pytest.raises(DBAPIError):
        await _run(app_engine, module, f"INSERT INTO {module}.product {columns} VALUES {values}")


async def test_unique_among_active_records(
    module: str, build: Declare, app_engine: AsyncEngine
) -> None:
    await build(module, _tables(module))
    first = await _run(
        app_engine, module, f"INSERT INTO {module}.product (name) VALUES ('Chair') RETURNING sys_id"
    )
    with pytest.raises(DBAPIError):
        await _run(app_engine, module, f"INSERT INTO {module}.product (name) VALUES ('Chair')")
    await _run(
        app_engine,
        module,
        f"UPDATE {module}.product SET deleted = now() WHERE sys_id = :r",
        r=first[0],
    )
    await _run(app_engine, module, f"INSERT INTO {module}.product (name) VALUES ('Chair')")


async def test_fields_are_added_and_widened(
    module: str, build: Declare, app_engine: AsyncEngine
) -> None:
    await build(module, _tables(module))
    done = await build(
        module,
        _tables(
            module,
            name=String("Name", max_length=80, required=True, unique=True),
            stock=Integer("Stock"),
            state=Choice("State", {"draft": "Draft", "sold": "Sold", "gone": "Gone"}),
        ),
    )
    assert f"Add {module}.product.stock" in done
    assert f"Widen {module}.product.name to character varying(80)" in done
    await _run(
        app_engine,
        module,
        f"INSERT INTO {module}.product (name, stock, state) VALUES (:n, 3, 'gone')",
        n="x" * 80,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"name": String("Name", max_length=10, required=True, unique=True)},
        {"price": Integer("Price")},
        {"state": Choice("State", {"draft": "Draft"})},
    ],
)
async def test_changes_that_lose_values_are_refused(
    module: str, build: Declare, change: dict[str, Field]
) -> None:
    await build(module, _tables(module))
    with pytest.raises(Problem) as refused:
        await build(module, _tables(module, **change))
    assert refused.value.code == "unsafe_change"


async def test_a_removed_field_is_retired_with_its_data(
    module: str, build: Declare, app_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    await build(module, _tables(module, note=Text("Note", required=True)))
    await _run(
        app_engine, module, f"INSERT INTO {module}.product (name, note) VALUES ('Shelf', 'oak')"
    )
    await build(module, _tables(module))
    # Not required any more, and the old values are still there.
    await _run(app_engine, module, f"INSERT INTO {module}.product (name) VALUES ('Box')")
    kept = await _run(app_engine, module, f"SELECT note FROM {module}.product WHERE name = 'Shelf'")
    assert kept[0] == "oak"
    with working_as(Context(channel="test")):
        async with owner_transaction(owner_engine) as conn:
            catalog = await load_catalog(conn)
    note = next(f for f in catalog.tables[f"{module}.product"].fields if f.name == "note")
    assert note.retired
    # Declared again, it comes back with its data.
    await build(module, _tables(module, note=Text("Note")))


async def test_a_field_becomes_required_only_if_no_active_record_leaves_it_empty(
    module: str, build: Declare, app_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    await build(module, _tables(module))
    await _run(app_engine, module, f"INSERT INTO {module}.product (name) VALUES ('Bare')")
    with pytest.raises(Problem) as refused:
        await build(
            module, _tables(module, price=Decimal("Price", precision=10, scale=2, required=True))
        )
    assert refused.value.code == "schema_change_failed"
    assert "required" in str(refused.value.detail)
    # All or nothing: the record of the field didn't change either.
    with working_as(Context(channel="test")):
        async with owner_transaction(owner_engine) as conn:
            catalog = await load_catalog(conn)
    price = next(f for f in catalog.tables[f"{module}.product"].fields if f.name == "price")
    assert not price.field.required


async def test_a_table_still_referenced_can_not_be_retired(module: str, build: Declare) -> None:
    await build(module, _tables(module))
    with pytest.raises(Problem) as refused:
        await build(module, [_tables(module)[1]])
    assert refused.value.code == "unknown_table"


async def test_declarations_are_checked_first(module: str, build: Declare) -> None:
    bad = Table(
        "thing",
        "Thing",
        {
            "created": String("Created"),
            "Bad Name": String("Bad"),
            "size": String("Size", max_length=0),
            "kind": Choice("Kind", {"a": "A"}, default="b"),
        },
    )
    with pytest.raises(Problem) as refused:
        await build(module, [bad])
    assert refused.value.code == "invalid_declaration"
    assert sorted(e.code for e in refused.value.errors) == [
        "bad_default",
        "bad_name",
        "bad_name",
        "bad_option",
    ]


async def test_the_engine_refuses_a_narrowing_even_if_a_record_asks(
    module: str, build: Declare, owner_engine: AsyncEngine
) -> None:
    await build(module, _tables(module))
    with working_as(Context(channel="test")):
        async with owner_transaction(owner_engine) as conn:
            await conn.execute(
                text(
                    "UPDATE sys.field SET options = CAST(:o AS jsonb) WHERE name = 'name'"
                    ' AND "table" = (SELECT sys_id FROM sys."table"'
                    " WHERE \"schema\" = :s AND name = 'product')"
                ),
                {"o": json.dumps({"max_length": 5}), "s": module},
            )
            with pytest.raises(Problem) as refused:
                await apply(conn, await load_catalog(conn))
            await conn.rollback()
    assert refused.value.code == "unsafe_change"


async def test_metadata_changes_are_audited(
    module: str, build: Declare, owner_engine: AsyncEngine
) -> None:
    await build(module, _tables(module))
    with working_as(Context(channel="test")):
        async with owner_transaction(owner_engine) as conn:
            channel = await conn.scalar(
                text(
                    'SELECT a.channel FROM sys.audit_log a JOIN sys."table" t'
                    " ON t.sys_id = a.record_id WHERE t.\"schema\" = :s AND t.name = 'product'"
                ),
                {"s": module},
            )
    assert channel == "install"


async def test_every_process_learns_about_a_change(
    module: str, build: Declare, app_engine: AsyncEngine
) -> None:
    await build(module, _tables(module))
    cache = MetadataCache(app_engine)
    await cache.start()
    try:
        with working_as(Context(channel="test")):
            before = await cache.catalog()
            assert before.tables[f"{module}.product"].label == {"en": "Product"}
            changed = asyncio.Event()
            cache.on_change(changed.set)
            renamed = _tables(module)
            renamed[1] = Table("product", "Article", renamed[1].fields)
            await build(module, renamed)
            await asyncio.wait_for(changed.wait(), 5)
            after = await cache.catalog()
        assert after.tables[f"{module}.product"].label == {"en": "Article"}
        assert after is await cache.catalog()  # kept until the next change
    finally:
        await cache.stop()
