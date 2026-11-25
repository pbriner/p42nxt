"""The records service on a declared table: changes with ETags, lists with the filter language,
sorting and paging, and undo. No HTTP: this is what the API will call."""

import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal as Number
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.conditions import Situation
from p42cloud.data.declarations import Table
from p42cloud.data.metadata import declare
from p42cloud.data.records import Record, Records
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.date import Date
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText
from p42cloud.data.undo import undo
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem

ANNA = uuid.UUID("01900000-0000-7000-8000-00000000a001")
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _shop(schema: str) -> list[Table]:
    return [
        Table("category", "Category", {"name": String("Name", max_length=40, required=True)}),
        Table(
            "product",
            "Product",
            {
                "name": String("Name", max_length=40, required=True, unique=True),
                "price": Decimal("Price", precision=10, scale=2),
                "state": Choice("State", {"draft": "Draft", "sold": "Sold"}),
                "category": Reference("Category", f"{schema}.category"),
                "made": Date("Made"),
                "seen": DateTime("Last seen"),
                "title": TranslatedText("Title"),
                "active": Boolean("Active"),
                "stock": Integer("Stock"),
                "kind": Choice("Kind", {"goods": "Goods", "service": "Service"}, default="goods"),
            },
        ),
    ]


@asynccontextmanager
async def _records(
    engine: AsyncEngine, channel: str = "ui", language: str = "en"
) -> AsyncGenerator[Records]:
    with working_as(Context(channel=channel, person_id=ANNA, language=language, access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            catalog = await load_catalog(conn)
            yield Records(conn, catalog, Situation(ANNA, language, now=NOW))


@pytest.fixture(scope="module")
async def shop(
    new_module: Callable[[], Awaitable[str]], upgraded: Settings
) -> AsyncIterator[tuple[str, AsyncEngine]]:
    schema = await new_module()
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="install")):
        async with owner_transaction(owner) as conn:
            await declare(conn, schema, _shop(schema))
    await owner.dispose()
    engine = create_engine(upgraded.database_url)
    product = f"{schema}.product"
    async with _records(engine) as records:
        desks = await records.create(f"{schema}.category", {"name": "Desks"})
        chairs = await records.create(f"{schema}.category", {"name": "Chairs"})
        rows: list[dict[str, Any]] = [
            {
                "name": "Oak desk",
                "price": "199.50",
                "state": "draft",
                "category": desks.sys_id,
                "made": "2026-11-10",
                "seen": "2026-11-13T09:00:00Z",
                "title": {"en": "Desk", "de": "Pult"},
                "active": True,
                "stock": 3,
            },
            {
                "name": "Müller chair",
                "price": 49,
                "state": "sold",
                "category": chairs.sys_id,
                "made": "2026-10-01",
                "seen": "2026-11-12T23:30:00Z",
                "title": "Chair",
                "active": False,
                "stock": 0,
            },
            {"name": "Lamp", "stock": 10},
            {
                "name": "Desk lamp",
                "price": "25",
                "state": "draft",
                "category": desks.sys_id,
                "made": "2026-11-13",
                "seen": "2026-11-13T12:00:00Z",
                "title": {"en": "Lamp", "de": "Leuchte"},
                "active": True,
            },
        ]
        for row in rows:
            await records.create(product, row)
        gone = await records.create(product, {"name": "Old box"})
        await records.delete(product, gone.sys_id, gone.etag)
    yield schema, engine
    await engine.dispose()


def _names(found: list[Record]) -> set[str]:
    return {r.values["name"] for r in found}


# ---- changing records ----


async def test_create_checks_values_and_fills_defaults(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    async with _records(engine) as records:
        made = await records.create(f"{schema}.product", {"name": "Stool", "price": "12.345"})
    assert made.values["price"] == Number("12.35")
    assert made.values["kind"] == "goods"
    assert made.values["created_by"] == ANNA
    assert made.etag.startswith('"')


async def test_create_reports_every_problem_at_once(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    with pytest.raises(Problem) as refused:
        async with _records(engine) as records:
            await records.create(
                f"{schema}.product",
                {"price": "lots", "state": "lost", "created_by": ANNA, "colour": "red"},
            )
    assert refused.value.status == 422
    assert sorted((e.field, e.code) for e in refused.value.errors) == [
        ("colour", "unknown_field"),
        ("created_by", "read_only"),
        ("name", "required"),
        ("price", "invalid"),
        ("state", "invalid"),
    ]


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"name": "Lamp"}, ("name", "not_unique")),
        ({"name": "Ghost", "category": uuid.uuid7()}, ("category", "no_such_record")),
    ],
)
async def test_the_databases_refusals_become_field_errors(
    shop: tuple[str, AsyncEngine], values: dict[str, Any], error: tuple[str, str]
) -> None:
    schema, engine = shop
    with pytest.raises(Problem) as refused:
        async with _records(engine) as records:
            await records.create(f"{schema}.product", values)
    assert [(e.field, e.code) for e in refused.value.errors] == [error]


async def test_changes_need_the_current_version(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        made = await records.create(key, {"name": "Bench", "stock": 1})
        with pytest.raises(Problem) as missing:
            await records.update(key, made.sys_id, {"stock": 2}, None)
        changed = await records.update(key, made.sys_id, {"stock": 2}, made.etag)
        with pytest.raises(Problem) as stale:
            await records.update(key, made.sys_id, {"stock": 3}, made.etag)
        same = await records.update(key, changed.sys_id, {"stock": 2}, changed.etag)
    assert missing.value.status == 428
    assert stale.value.status == 412
    assert stale.value.extra["current"]["stock"] == 2
    assert changed.values["stock"] == 2
    assert changed.etag != made.etag
    assert same.etag == changed.etag  # nothing changed, so the version stays


async def test_delete_and_restore(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        made = await records.create(key, {"name": "Crate"})
        deleted = await records.delete(key, made.sys_id, made.etag)
        assert deleted.values["deleted_by"] == ANNA
        for refused in (
            records.delete(key, made.sys_id, deleted.etag),
            records.update(key, made.sys_id, {"stock": 1}, deleted.etag),
        ):
            with pytest.raises(Problem) as conflict:
                await refused
            assert conflict.value.status == 409
        restored = await records.restore(key, made.sys_id)
        assert restored.values["deleted"] is None
        with pytest.raises(Problem):
            await records.restore(key, made.sys_id)


async def test_tables_and_fields_change_only_through_declarations(
    shop: tuple[str, AsyncEngine],
) -> None:
    _, engine = shop
    async with _records(engine) as records:
        tables = await records.list(
            "sys.table", where={"field": "name", "op": "eq", "value": "field"}
        )
        assert [r.values["schema"] for r in tables.records] == ["sys"]
        with pytest.raises(Problem) as refused:
            await records.create("sys.field_type", {"name": "money", "label": "Money"})
    assert refused.value.code == "declared_only"


async def test_changes_are_audited_with_the_requests_channel(
    shop: tuple[str, AsyncEngine],
) -> None:
    schema, engine = shop
    async with _records(engine, channel="api:token:sync") as records:
        made = await records.create(f"{schema}.product", {"name": "Rack"})
        await records.conn.execute(text("SET LOCAL ROLE p42_mod_sys"))
        channel = await records.conn.scalar(
            text("SELECT channel FROM sys.audit_log WHERE record_id = :r"), {"r": made.sys_id}
        )
    assert channel == "api:token:sync"


# ---- lists ----

ALL = {"Oak desk", "Müller chair", "Lamp", "Desk lamp"}


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({}, ALL),
        ({"field": "name", "op": "contains", "value": "DESK"}, {"Oak desk", "Desk lamp"}),
        ({"field": "name", "op": "contains", "value": "muller"}, {"Müller chair"}),
        ({"field": "name", "op": "eq", "value": "lamp"}, {"Lamp"}),
        ({"field": "name", "op": "starts_with", "value": "desk"}, {"Desk lamp"}),
        ({"field": "name", "op": "in", "value": ["lamp", "oak desk"]}, {"Lamp", "Oak desk"}),
        ({"field": "state", "op": "in", "value": ["draft"]}, {"Oak desk", "Desk lamp"}),
        ({"field": "state", "op": "ne", "value": "sold"}, {"Oak desk", "Lamp", "Desk lamp"}),
        ({"field": "state", "op": "not_in", "value": ["draft"]}, {"Müller chair", "Lamp"}),
        ({"field": "state", "op": "empty"}, {"Lamp"}),
        ({"field": "price", "op": "gt", "value": "30"}, {"Oak desk", "Müller chair"}),
        ({"field": "price", "op": "between", "value": [25, "50"]}, {"Müller chair", "Desk lamp"}),
        ({"not": {"field": "price", "op": "gt", "value": 30}}, {"Lamp", "Desk lamp"}),
        (
            {"or": [{"field": "stock", "op": "eq", "value": 0}, {"field": "stock", "op": "empty"}]},
            {"Müller chair", "Desk lamp"},
        ),
        ({"field": "category.name", "op": "eq", "value": "desks"}, {"Oak desk", "Desk lamp"}),
        ({"field": "category.name", "op": "ne", "value": "Desks"}, {"Müller chair", "Lamp"}),
        ({"field": "made", "op": "gte", "value": "@today-7d"}, {"Oak desk", "Desk lamp"}),
        ({"field": "made", "op": "lt", "value": "@start_of_month"}, {"Müller chair"}),
        ({"field": "seen", "op": "eq", "value": "@today"}, {"Oak desk", "Desk lamp"}),
        ({"field": "seen", "op": "eq", "value": "2026-11-12"}, {"Müller chair"}),
        ({"field": "seen", "op": "lt", "value": "@now"}, {"Oak desk", "Müller chair"}),
        (
            {"field": "seen", "op": "lte", "value": "@now"},
            {"Oak desk", "Müller chair", "Desk lamp"},
        ),
        ({"field": "seen", "op": "gt", "value": "2026-11-12"}, {"Oak desk", "Desk lamp"}),
        ({"field": "title", "op": "contains", "value": "lamp"}, {"Desk lamp"}),
        ({"field": "active", "op": "eq", "value": True}, {"Oak desk", "Desk lamp"}),
        ({"field": "active", "op": "ne", "value": True}, {"Müller chair", "Lamp"}),
        ({"field": "created_by", "op": "eq", "value": "@me"}, ALL),
        ({"and": []}, ALL),
        ({"or": []}, set[str]()),
    ],
)
async def test_the_filter_language(
    shop: tuple[str, AsyncEngine], condition: dict[str, Any], expected: set[str]
) -> None:
    schema, engine = shop
    async with _records(engine) as records:
        page = await records.list(f"{schema}.product", where=condition, limit=500)
    # Records made by other tests in this file are allowed; the seeded ones decide.
    assert _names(page.records) & (ALL | {"Old box"}) == expected


async def test_translated_text_in_the_persons_language(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    async with _records(engine, language="de") as records:
        page = await records.list(
            f"{schema}.product", where={"field": "title", "op": "contains", "value": "pult"}
        )
        fallback = await records.list(
            f"{schema}.product", where={"field": "title", "op": "eq", "value": "chair"}
        )
    assert _names(page.records) == {"Oak desk"}
    assert _names(fallback.records) == {"Müller chair"}  # no German title: English


@pytest.mark.parametrize(
    ("condition", "code"),
    [
        ({"field": "colour", "op": "eq", "value": "red"}, "unknown_field"),
        ({"field": "price", "op": "contains", "value": "1"}, "bad_operator"),
        ({"field": "price", "op": "gt", "value": "cheap"}, "bad_value"),
        ({"field": "seen", "op": "eq", "value": "2026-11-13T10:00"}, "bad_value"),
        ({"field": "name.first", "op": "eq", "value": "x"}, "not_a_reference"),
        ({"field": "price", "op": "between", "value": [1]}, "bad_value"),
        ({"field": "name", "op": "eq", "value": "x", "extra": 1}, "bad_shape"),
        ({"not": {"not": {"not": {"not": {"not": {"field": "name", "op": "empty"}}}}}}, "too_deep"),
        ({"and": [{"field": "name", "op": "empty"}] * 51}, "too_many"),
        ({"field": "category.name.x.y.z", "op": "eq", "value": "x"}, "too_far"),
    ],
)
async def test_conditions_are_refused_never_ignored(
    shop: tuple[str, AsyncEngine], condition: dict[str, Any], code: str
) -> None:
    schema, engine = shop
    with pytest.raises(Problem) as refused:
        async with _records(engine) as records:
            await records.list(f"{schema}.product", where=condition)
    assert refused.value.code == "invalid_condition"
    assert [e.code for e in refused.value.errors] == [code]


@pytest.mark.parametrize(
    "sort", [["-price", "name"], ["state", "-stock"], ["made"], ["-seen"], ["title"], []]
)
async def test_paging_by_cursor_gives_every_record_once_in_order(
    shop: tuple[str, AsyncEngine], sort: list[str]
) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    seeded = {"field": "name", "op": "in", "value": [n.lower() for n in ALL]}
    async with _records(engine) as records:
        whole = await records.list(key, where=seeded, sort=sort, limit=500)
        paged: list[Record] = []
        after = None
        while True:
            page = await records.list(key, where=seeded, sort=sort, limit=1, after=after)
            paged += page.records
            if page.next is None:
                break
            after = page.next
    assert [r.sys_id for r in paged] == [r.sys_id for r in whole.records]
    assert len(paged) == len(ALL)
    assert whole.total == len(ALL)
    assert whole.total_exact


async def test_empty_values_sort_last(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    seeded = {"field": "name", "op": "in", "value": [n.lower() for n in ALL]}
    async with _records(engine) as records:
        page = await records.list(f"{schema}.product", where=seeded, sort=["-price"])
    assert [r.values["name"] for r in page.records] == [
        "Oak desk",
        "Müller chair",
        "Desk lamp",
        "Lamp",
    ]


async def test_a_cursor_only_fits_its_own_list(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        first = await records.list(key, sort=["name"], limit=1)
        assert first.next is not None
        for after, sort in ((first.next, ["-name"]), ("not-a-cursor", ["name"])):
            with pytest.raises(Problem) as refused:
                await records.list(key, sort=sort, limit=1, after=after)
            assert refused.value.code == "bad_cursor"


async def test_deleted_records_are_listed_only_in_the_bin(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    async with _records(engine) as records:
        active = await records.list(f"{schema}.product", limit=500)
        binned = await records.list(f"{schema}.product", deleted=True, limit=500)
    assert "Old box" not in _names(active.records)
    assert "Old box" in _names(binned.records)


@pytest.mark.parametrize(
    ("sort", "code"),
    [(["colour"], "not_sortable"), (["-x"], "not_sortable"), (["category.x"], "not_a_column")],
)
async def test_only_known_fields_sort(
    shop: tuple[str, AsyncEngine], sort: list[str], code: str
) -> None:
    schema, engine = shop
    with pytest.raises(Problem) as refused:
        async with _records(engine) as records:
            await records.list(f"{schema}.product", sort=sort)
    assert refused.value.errors[0].code == code


# ---- undo ----


async def _request(engine: AsyncEngine, work: Callable[[Records], Awaitable[Any]]) -> uuid.UUID:
    context = Context(channel="ui", person_id=ANNA, access=EVERYTHING)
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            await work(Records(conn, await load_catalog(conn), Situation(ANNA, now=NOW)))
    return context.request_id


async def test_undo_reverses_everything_a_request_did(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        kept = await records.create(key, {"name": "Desk mat", "stock": 5})
        gone = await records.create(key, {"name": "Shelf"})
    made: list[Record] = []

    async def work(records: Records) -> None:
        made.append(await records.create(key, {"name": "Coat hook"}))
        await records.update(key, kept.sys_id, {"stock": 7, "price": "9.90"}, kept.etag)
        await records.delete(key, gone.sys_id, gone.etag)

    request = await _request(engine, work)
    async with _records(engine) as records:
        result = await undo(records.conn, records.catalog, request=request)
        assert result.skipped == []
        assert len(result.changed) == 3
        hook = await records.read(key, made[0].sys_id)
        mat = await records.read(key, kept.sys_id)
        shelf = await records.read(key, gone.sys_id)
        await records.conn.execute(text("SET LOCAL ROLE p42_mod_sys"))
        channel = await records.conn.scalar(
            text(
                "SELECT channel FROM sys.audit_log WHERE record_id = :r"
                " ORDER BY seq DESC NULLS FIRST LIMIT 1"
            ),
            {"r": kept.sys_id},
        )
    assert hook.values["deleted"] is not None  # undoing a create deletes
    assert (mat.values["stock"], mat.values["price"]) == (5, None)
    assert shelf.values["deleted"] is None  # undoing a delete restores
    assert channel == f"ui:undo:{request}"


async def test_undo_skips_fields_changed_again_since(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        made = await records.create(key, {"name": "Hanger", "stock": 1, "price": "5"})
    versions = [made]

    async def first(records: Records) -> None:
        versions.append(
            await records.update(key, made.sys_id, {"stock": 2, "price": "6"}, made.etag)
        )

    async def second(records: Records) -> None:
        await records.update(key, made.sys_id, {"stock": 3}, versions[-1].etag)

    request = await _request(engine, first)
    await _request(engine, second)
    async with _records(engine) as records:
        result = await undo(records.conn, records.catalog, request=request)
        now = await records.read(key, made.sys_id)
    assert [(s.field, s.reason) for s in result.skipped] == [
        ("stock", "It was changed again since.")
    ]
    assert (now.values["stock"], now.values["price"]) == (3, Number("5.00"))


async def test_undoing_an_undo_redoes(shop: tuple[str, AsyncEngine]) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        made = await records.create(key, {"name": "Peg", "stock": 1})

    async def change(records: Records) -> None:
        await records.update(key, made.sys_id, {"stock": 9}, made.etag)

    async def take_back(records: Records) -> None:
        await undo(records.conn, records.catalog, request=changed)

    changed = await _request(engine, change)
    undone = await _request(engine, take_back)
    async with _records(engine) as records:
        assert (await records.read(key, made.sys_id)).values["stock"] == 1
        await undo(records.conn, records.catalog, request=undone)
        assert (await records.read(key, made.sys_id)).values["stock"] == 9


async def test_undo_skips_what_the_database_refuses_in_plain_words(
    shop: tuple[str, AsyncEngine],
) -> None:
    schema, engine = shop
    key = f"{schema}.product"
    async with _records(engine) as records:
        made = await records.create(key, {"name": "Tray"})

    async def remove(records: Records) -> None:
        await records.delete(key, made.sys_id, made.etag)

    removed = await _request(engine, remove)
    async with _records(engine) as records:
        await records.create(key, {"name": "Tray"})  # the name is taken again
        result = await undo(records.conn, records.catalog, request=removed)
    assert [s.reason for s in result.skipped] == [
        "name: Another active record already has this value."
    ]
    assert result.changed == []
