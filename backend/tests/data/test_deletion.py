"""Deleting a record others point to: refused, taken along, emptied or kept, as each reference
declares; previewed and confirmed; restored as one."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table, check
from p42cloud.data.presenting import deletion_preview
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from tests.security.helpers import acting


def _module(module_id: str) -> Module:
    def name() -> dict[str, Any]:
        return {"name": String("Name", max_length=40)}

    m = module_id
    return Module(
        id=m,
        publisher="test",
        name="Deletion",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(
            Table("partner", "Partner", name(), display="name"),
            Table("statement", "Statement", name(), display="name"),
            Table(
                "line",
                "Line",
                name()
                | {
                    "statement": Reference("Statement", f"{m}.statement", on_delete="cascade"),
                    "partner": Reference("Partner", f"{m}.partner", on_delete="restrict"),
                },
                display="name",
            ),
            # Lines of lines: a cascade goes on.
            Table(
                "split",
                "Split",
                name() | {"line": Reference("Line", f"{m}.line", on_delete="cascade")},
                display="name",
            ),
            Table(
                "note",
                "Note",
                name()
                | {
                    "statement": Reference("Statement", f"{m}.statement", on_delete="clear"),
                    "about": Reference("About", f"{m}.statement"),  # kept
                },
                display="name",
            ),
        ),
    )


@pytest.fixture(scope="module")
async def app(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def made(
    owner_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]], app: AsyncEngine
) -> dict[str, Any]:
    """A statement with two lines (one split), a note emptied and one kept; a partner in use."""
    m = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(m)])
    ids: dict[str, Any] = {"m": m}
    async with acting(app, None) as records:

        async def make(table: str, label: str, **values: Any) -> None:
            ids[label] = (await records.create(f"{m}.{table}", {"name": label} | values)).sys_id

        await make("partner", "Shop")
        await make("partner", "Unused")
        await make("statement", "March")
        await make("statement", "April")
        await make("line", "Rent", statement=ids["March"])
        await make("line", "Food", statement=ids["March"], partner=ids["Shop"])
        await make("line", "Other month", statement=ids["April"])
        await make("split", "Half", line=ids["Rent"])
        await make("note", "Check", statement=ids["March"], about=ids["March"])
    # A line deleted on its own before: restoring March mustn't bring it back.
    async with acting(app, None) as records:
        await make("line", "Old", statement=ids["March"])
        old = await records.read(f"{m}.line", ids["Old"])
        await records.delete(f"{m}.line", ids["Old"], old.etag)
    return ids


async def _preview(app: AsyncEngine, ids: dict[str, Any], table: str, label: str) -> Any:
    async with acting(app, None) as records:
        return await deletion_preview(records, f"{ids['m']}.{table}", ids[label])


async def _delete(
    app: AsyncEngine, ids: dict[str, Any], table: str, label: str, confirm: Any
) -> Any:
    async with acting(app, None) as records:
        key = f"{ids['m']}.{table}"
        current = await records.read(key, ids[label])
        return await records.delete(key, ids[label], current.etag, confirm)


async def _active(app: AsyncEngine, ids: dict[str, Any], table: str) -> set[str]:
    async with acting(app, None) as records:
        page = await records.list(f"{ids['m']}.{table}", limit=100)
    return {str(r.values["name"]) for r in page.records}


async def test_a_delete_takes_its_lines_along_after_a_confirmation(
    app: AsyncEngine, made: dict[str, Any]
) -> None:
    preview = await _preview(app, made, "statement", "March")
    lines, splits = preview["cascade"]
    assert (lines["table"], lines["count"], splits["count"]) == (f"{made['m']}.line", 2, 1)
    assert {r["name"] for r in lines["records"]} == {"Rent", "Food"}
    assert [(g["field"], g["count"]) for g in preview["clear"]] == [("statement", 1)]
    assert [(g["field"], g["count"]) for g in preview["kept"]] == [("about", 1)]
    assert preview["blocked"] == [] and preview["changes"] == 4

    # Without the fingerprint, or with another one: refused, nothing changed.
    for confirm in ("", "0123456789abcdef"):
        with pytest.raises(Problem) as refused:
            await _delete(app, made, "statement", "March", confirm)
        assert refused.value.code == "confirm"
        assert refused.value.extra["fingerprint"] == preview["fingerprint"]
    assert "March" in await _active(app, made, "statement")

    await _delete(app, made, "statement", "March", preview["fingerprint"])
    assert await _active(app, made, "statement") == {"April"}
    assert await _active(app, made, "line") == {"Other month"}
    assert await _active(app, made, "split") == set()
    async with acting(app, None) as records:
        note = await records.read(f"{made['m']}.note", made["Check"])
    assert (note.values["statement"], note.values["about"]) == (None, made["March"])

    # Restored as one: its lines and their split, not the line deleted before.
    async with acting(app, None) as records:
        await records.restore(f"{made['m']}.statement", made["March"])
    assert await _active(app, made, "line") == {"Rent", "Food", "Other month"}
    assert await _active(app, made, "split") == {"Half"}


async def test_a_record_in_use_is_kept_until_nothing_points_to_it(
    app: AsyncEngine, made: dict[str, Any], owner_engine: AsyncEngine
) -> None:
    preview = await _preview(app, made, "partner", "Shop")
    assert [(g["field"], g["count"], g["records"][0]["name"]) for g in preview["blocked"]] == [
        ("partner", 1, "Food")
    ]
    with pytest.raises(Problem) as refused:
        await _delete(app, made, "partner", "Shop", "")
    assert refused.value.code == "in_use"
    assert [e.code for e in refused.value.errors] == ["in_use"]

    # The database refuses it too, however it's written.
    table = f"{made['m']}.partner"
    with pytest.raises(Exception, match="still point to it"), working_as(Context(channel="test")):
        async with owner_transaction(owner_engine) as conn:
            await conn.execute(
                text(f"UPDATE {table} SET deleted = now() WHERE sys_id = :id"),
                {"id": made["Shop"]},
            )

    # Nothing points to it: deleted as before, no confirmation needed.
    assert (await _preview(app, made, "partner", "Unused"))["changes"] == 0
    await _delete(app, made, "partner", "Unused", "")
    assert await _active(app, made, "partner") == {"Shop"}


async def test_module_code_deletes_without_a_confirmation(
    app: AsyncEngine, made: dict[str, Any]
) -> None:
    await _delete(app, made, "statement", "April", None)
    assert await _active(app, made, "line") == {"Rent", "Food"}


def test_a_required_reference_cant_be_emptied() -> None:
    table = Table("t", "T", {"up": Reference("Up", "sys.person", on_delete="clear", required=True)})
    with pytest.raises(Problem) as refused:
        check("zz", [table])
    assert [e.code for e in refused.value.errors] == ["bad_option"]
