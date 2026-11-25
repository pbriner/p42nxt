"""Calculated fields and roll-ups: worked out by the database on every save, kept right when
lines are made, changed, moved, deleted and restored, and never typed."""

import io
import json
import zipfile
from collections.abc import AsyncGenerator, Awaitable, Callable
from decimal import Decimal as D
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data import exporting
from p42cloud.data.declarations import Table
from p42cloud.data.exporting import export
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.date import Date
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.formula import Formula
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.rollup import Rollup
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.security.access.roles import Grant, Role
from tests.security.helpers import acting, role_id


def _module(module_id: str, gross: str = "net * (1 + vat)") -> Module:
    statement = Table(
        "statement",
        "Statement",
        {
            "title": String("Title", max_length=60),
            "net": Decimal("Net"),
            "vat": Decimal("VAT rate", precision=6, scale=4),
            "gross": Formula("Gross", gross),
            "issued": Date("Issued"),
            "due": Date("Due"),
            "days": Formula("Days to pay", "due - issued", scale=0),
            "total": Rollup("Total", f"{module_id}.line", "statement", field="amount"),
            "lines": Rollup("Lines", f"{module_id}.line", "statement", measure="count", scale=0),
            "charges": Rollup(
                "Charges",
                f"{module_id}.line",
                "statement",
                field="amount",
                where={"kind": "charge"},
            ),
        },
    )
    line = Table(
        "line",
        "Line",
        {
            "statement": Reference("Statement", f"{module_id}.statement"),
            "amount": Decimal("Amount"),
            "kind": Choice("Kind", {"charge": "Charge", "credit": "Credit"}, default="charge"),
        },
    )
    return Module(
        id=module_id,
        publisher="test",
        name="Totals",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(statement, line),
        roles=(Role(f"{module_id}.liner", "Liner", (Grant(f"{module_id}.line", ("read",)),)),),
    )


@pytest.fixture(scope="module")
async def app_engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def module_id(owner_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]]) -> str:
    made = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(made)])
    return made


async def _get(engine: AsyncEngine, key: str, sys_id: Any) -> dict[str, Any]:
    async with acting(engine, None) as records:
        return dict((await records.read(key, sys_id)).values)


async def test_a_formula_is_worked_out_on_every_save(
    app_engine: AsyncEngine, module_id: str
) -> None:
    key = f"{module_id}.statement"
    async with acting(app_engine, None) as records:
        made = await records.create(
            key, {"title": "May", "net": "100", "vat": "0.081", "issued": "2026-05-01"}
        )
    assert made.values["gross"] == D("108.10")
    assert made.values["days"] is None  # no due date yet: nothing to subtract
    async with acting(app_engine, None) as records:
        changed = await records.update(
            key, made.sys_id, {"net": "200", "due": "2026-05-31"}, made.etag
        )
    assert (changed.values["gross"], changed.values["days"]) == (D("216.20"), D("30"))
    with pytest.raises(Problem) as refused:
        async with acting(app_engine, None) as records:
            await records.update(key, made.sys_id, {"gross": "1"}, changed.etag)
    assert [e.field for e in refused.value.errors] == ["gross"]


async def test_rollups_follow_their_lines(app_engine: AsyncEngine, module_id: str) -> None:
    statements, lines = f"{module_id}.statement", f"{module_id}.line"
    async with acting(app_engine, None) as records:
        may = (await records.create(statements, {"title": "May"})).sys_id
        june = (await records.create(statements, {"title": "June"})).sys_id
        first = await records.create(lines, {"statement": may, "amount": "40"})
        await records.create(lines, {"statement": may, "amount": "15", "kind": "credit"})
    found = await _get(app_engine, statements, may)
    assert (found["total"], found["lines"], found["charges"]) == (D("55.00"), D("2"), D("40.00"))

    async with acting(app_engine, None) as records:  # changed, then moved to June
        first = await records.update(lines, first.sys_id, {"amount": "50"}, first.etag)
        first = await records.update(lines, first.sys_id, {"statement": june}, first.etag)
    assert (await _get(app_engine, statements, may))["total"] == D("15.00")
    assert (await _get(app_engine, statements, june))["total"] == D("50.00")

    async with acting(app_engine, None) as records:  # deleted, then restored
        await records.delete(lines, first.sys_id, first.etag)
    june_now = await _get(app_engine, statements, june)
    assert (june_now["total"], june_now["lines"]) == (D("0.00"), D("0"))
    async with acting(app_engine, None) as records:
        await records.restore(lines, first.sys_id)
    assert (await _get(app_engine, statements, june))["total"] == D("50.00")


async def test_a_formula_that_cant_be_worked_out_is_refused(
    owner_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    made = await new_module()
    for wrong in ("net * nothing", "issued + 1", "net +", "drop(net)"):
        with pytest.raises(Problem) as refused, working_as(Context(channel="install")):
            await install_modules(owner_engine, [CORE, _module(made, gross=wrong)])
        assert refused.value.code == "bad_formula", wrong


async def test_lists_show_and_sort_by_fields_of_referenced_records(
    app_engine: AsyncEngine, module_id: str
) -> None:
    statements, lines = f"{module_id}.statement", f"{module_id}.line"
    async with acting(app_engine, None) as records:
        for title, net in (("Small", "10"), ("Big", "900"), ("Middle", "50")):
            made = await records.create(statements, {"title": title, "net": net, "vat": "0"})
            await records.create(lines, {"statement": made.sys_id, "amount": "1"})
        group = await records.create("sys.group", {"name": f"Liners {module_id}"})
        await records.create(
            "sys.group_role",
            {"group": group.sys_id, "role": await role_id(records, f"{module_id}.liner")},
        )
        liner = await records.create("sys.person", {"name": f"Liner {module_id}"})
        await records.create("sys.membership", {"group": group.sys_id, "person": liner.sys_id})
    seen: list[Any] = []
    after = None
    while True:  # one record a page: the page link carries the referenced value
        async with acting(app_engine, None) as records:
            page = await records.list(
                lines,
                columns=["statement.title"],
                sort=["-statement.gross"],
                limit=1,
                after=after,
            )
            seen += [records.to_json(records.catalog.tables[lines], r) for r in page.records]
        after = page.next
        if after is None:
            break
    assert [s["statement.title"] for s in seen] == ["Big", "Middle", "Small"]
    assert seen[0]["statement.gross"] == "900.00"
    async with acting(app_engine, liner.sys_id) as records:  # may read lines, not statements
        page = await records.list(lines, columns=["statement.title"])
    assert {r.walked["statement.title"][1] for r in page.records} == {None}
    with pytest.raises(Problem):
        async with acting(app_engine, None) as records:
            await records.list(lines, columns=["statement.nothing"])


async def test_a_list_is_exported_as_csv_excel_or_json(
    app_engine: AsyncEngine, module_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    statements, lines = f"{module_id}.statement", f"{module_id}.line"
    async with acting(app_engine, None) as records:
        for title in ("Zürich", "Bern"):
            made = await records.create(statements, {"title": title, "net": "100", "vat": "0.1"})
            await records.create(lines, {"statement": made.sys_id, "amount": "7", "kind": "credit"})
    columns = ["amount", "kind", "statement.title", "statement.gross"]
    async with acting(app_engine, None) as records:
        made = await export(
            records, lines, file_format="csv", sort=["statement.title"], fields=columns
        )
    text_rows = made.content.decode("utf-8-sig").splitlines()
    assert text_rows == [
        "Amount,Kind,Statement > Title,Statement > Gross",
        "7.00,Credit,Bern,110.00",
        "7.00,Credit,Zürich,110.00",
    ]
    assert made.filename.endswith(".csv") and made.rows == 2

    async with acting(app_engine, None) as records:
        sheet = await export(records, lines, file_format="xlsx", fields=columns)
        data = await export(records, lines, file_format="json", fields=["kind"])
    with zipfile.ZipFile(io.BytesIO(sheet.content)) as book:
        cells = book.read("xl/worksheets/sheet1.xml").decode()
    assert "<v>110</v>" in cells  # a number Excel adds up, not text
    exported = json.loads(data.content)
    assert {r["_display"]["kind"] for r in exported["records"]} == {"Credit"}

    async with acting(app_engine, None) as records:
        logged = await records.conn.scalar(
            text(
                "SELECT count(*) FROM sys.audit_log WHERE operation = 'data_exported'"
                " AND changes ->> 'table' = :t"
            ),
            {"t": lines},
        )
    assert logged == 3

    monkeypatch.setattr(exporting, "MAX_ROWS", 1)
    with pytest.raises(Problem) as refused:
        async with acting(app_engine, None) as records:
            await export(records, lines, file_format="csv")
    assert refused.value.code == "too_many"
