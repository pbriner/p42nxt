"""Writing a module's list views into records, and checking what people save and remember."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import MAX_WALK, Filter, Query, Situation, field_of
from p42cloud.data.hooks import Change, TableHook
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import label_of, sync
from p42cloud.ui.views.models import ListView

SEARCHED_KINDS = frozenset({"text", "translated"})
# Kinds that make poor columns in a list made from a table's fields.
WIDE_TYPES = frozenset({"text", "markdown", "json", "secret"})
GENERATED_COLUMNS = 5
MAX_COLUMNS = 40
MAX_SAVED = 100


def _known(catalog: Catalog, table: TableDef, name: str) -> bool:
    """A field of the table, or of a record it points to (`partner.city`, at most 3 steps)."""
    parts = name.removeprefix("-").split(".")
    current: TableDef | None = table
    for i, part in enumerate(parts):
        f = field_of(current, part) if current is not None else None
        if f is None or i > MAX_WALK:
            return False
        target = f.type.references(f.field.options)
        current = catalog.table(target) if target else None
    return True


def _unknown(catalog: Catalog, table: TableDef, names: list[str]) -> list[str]:
    return [n for n in names if not _known(catalog, table, n)]


def _names(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):  # pyright: ignore[reportUnknownVariableType]
        raise _invalid(where, "A list of field names.")
    names: list[str] = value  # pyright: ignore[reportUnknownVariableType]
    if len(names) > MAX_COLUMNS:
        raise _invalid(where, f"At most {MAX_COLUMNS} fields.")
    return names


def _invalid(where: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(where, "invalid", message)],
    )


def _filter_problems(
    catalog: Catalog, situation: Situation, table: TableDef, condition: Any
) -> list[str]:
    try:
        Filter(catalog, situation, Query()).sql(table, "t", condition)
    except Problem as problem:
        return [e.message for e in problem.errors] or [problem.title]
    return []


def generated(table: TableDef, *, searchable: bool) -> ListView:
    """A table's default view made from its fields: its name first, then a few more."""
    display = table.display
    others = [
        f.name
        for f in table.active_fields()
        if f.name != display and f.field.type not in WIDE_TYPES
    ]
    columns = ([display] if display else []) + others[: GENERATED_COLUMNS - (1 if display else 0)]
    return ListView(table.key, tuple(columns), searchable=searchable)


def _view_problems(catalog: Catalog, module: str, view: ListView) -> list[FieldError]:
    where = f"views.{view.table}"
    table = catalog.table(view.table)
    if table is None or table.retired or table.schema != module:
        return [FieldError(where, "unknown_table", "A view is of one of the module's tables.")]
    found = [
        FieldError(where, "unknown_field", f"No field {n!r}.")
        for n in _unknown(
            catalog, table, [*view.columns, *view.sort, *view.available, *view.search]
        )
    ]
    if not view.columns:
        found.append(FieldError(where, "no_columns", "A view shows at least one column."))
    for name in view.search:
        f = field_of(table, name)
        if f is not None and f.type.kind not in SEARCHED_KINDS:
            found.append(FieldError(where, "not_text", f"{name} isn't text; it can't be searched."))
    if view.filter is not None:
        found += [
            FieldError(where, "bad_filter", m)
            for m in _filter_problems(catalog, Situation(uuid.UUID(int=0)), table, view.filter)
        ]
    return found


def _searched(table: TableDef, view: ListView) -> list[str]:
    if view.search:
        return list(view.search)
    return [
        n
        for n in view.columns
        if (f := field_of(table, n)) is not None
        and f.name != "sys_id"
        and f.type.kind in SEARCHED_KINDS
    ]


async def write_views(
    conn: AsyncConnection, catalog: Catalog, module: str, views: tuple[ListView, ...]
) -> None:
    """Brings the module's list views to its declaration; its other tables get made ones.

    The core's made views stay out of the search: its tables are settings and logs, and the
    core declares the few worth searching.
    """
    found = [p for v in views for p in _view_problems(catalog, module, v)]
    declared = {v.table for v in views}
    if len(declared) != len(views):
        found.append(FieldError("views", "duplicate", "One view per table."))
    if found:
        raise Problem(422, "invalid_views", "The list views can't be used.", errors=found)
    tables = [t for t in catalog.tables.values() if t.schema == module and not t.retired]
    every = [*views] + [
        generated(t, searchable=module != "sys") for t in tables if t.key not in declared
    ]
    rows: list[dict[str, Any]] = []
    for view in every:
        table = catalog.tables[view.table]
        rows.append(
            {
                "name": view.table,
                "table": view.table,
                "label": label_of(view.label) or table.label_plural or table.label,
                "columns": list(view.columns),
                "sort": list(view.sort),
                "filter": view.filter,
                "available": list(view.available) or None,
                "search": _searched(table, view) or None,
                "searchable": view.searchable,
            }
        )
    await sync(
        conn,
        "sys.list_view",
        module,
        rows,
        frozenset({"label", "columns", "sort", "filter", "available", "search"}),
    )


# ---- what people save and remember ----


async def _view_table(change: Change, table_name: str) -> TableDef:
    """The table of the list view a saved view or remembered list is about."""
    conn = change.records.conn
    view = change.values.get("view")
    if change.operation != "create":
        if "view" in change.values:
            raise _invalid("view", "A saved view stays with its list.")
        view = await conn.scalar(
            text(f"SELECT view FROM {table_name} WHERE sys_id = :id"),  # noqa: S608
            {"id": change.sys_id},
        )
    key = await conn.scalar(
        text('SELECT "table" FROM sys.list_view WHERE name = :v AND deleted IS NULL'),
        {"v": view},
    )
    found = change.records.catalog.table(str(key)) if key else None
    if found is None:
        raise _invalid("view", "No such list view.")
    return found


async def _check_state(change: Change, table_name: str) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["person"] = change.person
    table = await _view_table(change, table_name)
    for name in ("columns", "sort"):
        if name in change.values:
            unknown = _unknown(change.records.catalog, table, _names(change.values[name], name))
            if unknown:
                raise _invalid(name, f"No such fields: {', '.join(unknown)}.")
    if "filter" in change.values and change.values["filter"] is not None:
        records = change.records
        problems = _filter_problems(
            records.catalog, records.situation, table, change.values["filter"]
        )
        if problems:
            raise _invalid("filter", " ".join(problems))


async def _check_saved(change: Change) -> None:
    await _check_state(change, "sys.saved_view")
    name = change.values.get("name")
    if isinstance(name, str):
        change.values["name"] = " ".join(name.split())
        if not change.values["name"]:
            raise _invalid("name", "A saved view needs a name.")
    if change.operation == "create":
        count = await change.records.conn.scalar(
            text(
                "SELECT count(*) FROM sys.saved_view WHERE person = :p AND view = :v"
                " AND deleted IS NULL"
            ),
            {"p": change.person, "v": change.values.get("view")},
        )
        if (count or 0) >= MAX_SAVED:
            raise Problem(409, "too_many", f"At most {MAX_SAVED} saved views per list.")


async def _one_default(change: Change) -> None:
    """Only one of a person's saved views of a list opens by default."""
    if change.values.get("is_default") is not True or change.sys_id is None:
        return
    await change.records.conn.execute(
        text(
            "UPDATE sys.saved_view SET is_default = false WHERE is_default AND deleted IS NULL"
            " AND sys_id <> :id AND (person, view) ="
            " (SELECT person, view FROM sys.saved_view WHERE sys_id = :id)"
        ),
        {"id": change.sys_id},
    )


async def _check_remembered(change: Change) -> None:
    await _check_state(change, "sys.view_state")


TABLE_HOOKS = (
    TableHook("sys.saved_view", before=_check_saved, after=_one_default),
    TableHook("sys.view_state", before=_check_remembered),
)
