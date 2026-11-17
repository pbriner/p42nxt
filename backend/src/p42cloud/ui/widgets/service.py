"""Writing a module's widgets into records, a person's dashboard, and checking their layouts."""

import re
import uuid
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.conditions import Filter, Query, Situation, field_of
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.presenting import translated
from p42cloud.data.records import Records
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import label_of, sync
from p42cloud.ui.widgets.models import HOME, SIZES, Widget

WIDGET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MIN_REFRESH = 30
MAX_ITEMS = 60
MAX_PARAMS = 3
PARAM = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ID = re.compile(r"^[0-9a-fA-F-]{36}$")
STRENGTH = {"available": 0, "default": 1, "mandatory": 2}


def _field_problems(
    catalog: Catalog, where: str, table_key: str, widget: Widget
) -> list[FieldError]:
    table = catalog.table(table_key)
    if table is None:
        return [FieldError(where, "unknown_table", f"No table {table_key!r}.")]
    found: list[FieldError] = []
    checks = (
        (widget.field, ("number",), "A number field to add up."),
        (
            widget.group,
            ("choice", "reference", "boolean", "text", "date", "datetime"),
            "A field to group by.",
        ),
        (widget.trend, ("date", "datetime"), "A date field for the trend."),
        (widget.dated, ("date", "datetime"), "A date field for the time range."),
    )
    for name, kinds, message in checks:
        if name is None:
            continue
        f = field_of(table, name)
        if f is None or f.type.kind not in kinds:
            found.append(FieldError(where, "bad_field", f"{name}: {message}"))
    if widget.filter is not None:
        try:
            Filter(catalog, Situation(uuid.UUID(int=0)), Query()).sql(table, "t", widget.filter)
        except Problem as problem:
            found += [FieldError(where, e.code, e.message) for e in problem.errors]
    if widget.param is not None:
        name, target = widget.param
        f = field_of(table, name)
        if f is None or f.type.kind != "reference" or f.field.options.get("table") != target:
            found.append(
                FieldError(where, "bad_param", f"{name}: a reference to {target} to filter by.")
            )
    return found


def _shape_problems(where: str, module: str, widget: Widget) -> list[FieldError]:
    found: list[FieldError] = []
    if not WIDGET_NAME.match(widget.name):
        found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
    if widget.dashboard not in (HOME, module):
        found.append(FieldError(where, "bad_dashboard", "On home or the module's own dashboard."))
    if not widget.sizes or not set(widget.sizes) <= set(SIZES) or widget.size not in widget.sizes:
        found.append(FieldError(where, "bad_size", f"Sizes are some of {', '.join(SIZES)}."))
    if widget.accent is not None and not 1 <= widget.accent <= 8:  # noqa: PLR2004 - chart colours
        found.append(FieldError(where, "bad_accent", "An accent is chart colour 1 to 8."))
    if widget.refresh is not None and widget.refresh < MIN_REFRESH:
        found.append(
            FieldError(where, "bad_refresh", f"Refresh every {MIN_REFRESH} seconds or more.")
        )
    return found


def _source_problems(
    catalog: Catalog, views: dict[str, str], module: str, widget: Widget, sources: set[str]
) -> list[FieldError]:
    """A source widget: a GET route of its module, a list (who may see it), maybe a choice."""
    where = f"widgets.{widget.name}"
    found: list[FieldError] = []
    if widget.param is not None:
        name, table = widget.param
        if not WIDGET_NAME.match(name) or catalog.table(table) is None:
            found.append(FieldError(where, "bad_param", "A choice: a name and a table."))
    if widget.kind not in ("figure", "chart"):
        found.append(FieldError(where, "bad_source", "Only figures and charts have a source."))
    elif widget.source not in sources:
        found.append(
            FieldError(where, "unknown_source", f"{module} has no GET route {widget.source}.")
        )
    if widget.list is None or widget.list not in views:
        found.append(FieldError(where, "unknown_view", "It needs a list: who may see it."))
    return found


def _problems(
    catalog: Catalog, views: dict[str, str], module: str, widget: Widget, sources: set[str]
) -> list[FieldError]:
    where = f"widgets.{widget.name}"
    found = _shape_problems(where, module, widget)
    if widget.source is not None:
        return found + _source_problems(catalog, views, module, widget, sources)
    if widget.kind == "text":
        if widget.text is None:
            found.append(FieldError(where, "no_text", "A text widget has its text."))
        return found
    table_key = views.get(widget.list) if widget.list is not None else None
    if table_key is None:
        return [*found, FieldError(where, "unknown_view", f"No list view {widget.list!r}.")]
    if widget.param is not None and widget.kind == "list":
        found.append(FieldError(where, "bad_param", "Only figures and charts filter by a choice."))
    if widget.dated is not None and widget.kind != "chart":
        found.append(FieldError(where, "bad_dated", "Only charts offer a time range."))
    if widget.kind == "chart" and (widget.group is None or widget.chart is None):
        found.append(
            FieldError(where, "no_group", "A chart groups by a field, as a kind of chart.")
        )
    if widget.measure != "count" and widget.field is None:
        found.append(FieldError(where, "no_field", "A sum or average needs its field."))
    return found + _field_problems(catalog, where, table_key, widget)


async def write_widgets(
    conn: AsyncConnection, catalog: Catalog, module: str, widgets: tuple[Widget, ...]
) -> None:
    """Brings the module's widgets to its declaration (after the list views)."""
    views = {
        str(r.name): str(r.table)
        for r in await conn.execute(
            text('SELECT name, "table" FROM sys.list_view WHERE deleted IS NULL')
        )
    }
    sources = {
        str(r.path)
        for r in await conn.execute(
            text(
                "SELECT path FROM sys.module_route WHERE module = :m AND method = 'get'"
                " AND deleted IS NULL"
            ),
            {"m": module},
        )
    }
    found = [p for w in widgets for p in _problems(catalog, views, module, w, sources)]
    if len({w.name for w in widgets}) != len(widgets):
        found.append(FieldError("widgets", "duplicate", "A widget's name is used twice."))
    if found:
        raise Problem(422, "invalid_widgets", "The widgets can't be used.", errors=found)
    rows = [
        {
            "name": f"{module}.{w.name}",
            "title": label_of(w.title),
            "kind": w.kind,
            "dashboard": w.dashboard,
            "list": w.list,
            "source_route": w.source,
            "param": w.param[0] if w.param else None,
            "param_table": w.param[1] if w.param else None,
            "filter": w.filter,
            "measure": w.measure,
            "field": w.field,
            "group": w.group,
            "period": w.period,
            "chart": w.chart,
            "text": label_of(w.text),
            "icon": w.icon,
            "accent": w.accent,
            "filled": w.filled,
            "target": w.target,
            "trend": w.trend,
            "dated": w.dated,
            "sizes": list(w.sizes),
            "size": w.size,
            "refresh": w.refresh,
            "shown": w.shown,
            "position": position,
        }
        for position, w in enumerate(widgets)
    ]
    await sync(conn, "sys.widget", module, rows, frozenset({"title", "filter", "text", "sizes"}))


# ---- a person's dashboard ----


def _definition(row: Any, table: str | None, language: str, shown: str) -> dict[str, Any]:
    return {
        "name": row.name,
        "module": row.module,
        "dashboard": row.dashboard,  # where it's placed by default
        "title": translated(row.title, language),
        "kind": row.kind,
        "list": row.list,
        "source": f"/api/v1/{row.module}{row.source_route}" if row.source_route else None,
        "param": row.param,
        "param_table": row.param_table,
        "table": table,
        "filter": row.filter,
        "measure": row.measure,
        "field": row.field,
        "group": row.group,
        "period": row.period,
        "chart": row.chart,
        "text": translated(row.text, language),
        "icon": row.icon,
        "accent": row.accent,
        "filled": row.filled,
        "target": str(row.target) if row.target is not None else None,
        "trend": row.trend,
        "dated": row.dated,
        "sizes": row.sizes,
        "size": row.size,
        "refresh": row.refresh,
        "shown": shown,
    }


async def _offered(records: Records, dashboard: str) -> list[dict[str, Any]]:
    """Every widget the person may see, for this dashboard: its own first. A widget is placed by
    default (or always) only on its own dashboard; on the others it's available."""
    conn = records.conn
    await switch_role(conn, "sys")
    rows = list(
        await conn.execute(
            text(
                'SELECT w.*, v."table" AS view_table FROM sys.widget w'
                " JOIN sys.module m ON m.name = w.module"
                " LEFT JOIN sys.list_view v ON v.name = w.list AND v.deleted IS NULL"
                " WHERE w.deleted IS NULL AND m.enabled AND m.deleted IS NULL"
                " ORDER BY w.dashboard = :d DESC, w.module, w.position, w.name"
            ),
            {"d": dashboard},
        )
    )
    audiences: dict[str, str] = {}
    groups = list(records.access.groups)
    for row in await conn.execute(
        text(
            "SELECT widget, shown FROM sys.widget_audience"
            ' WHERE deleted IS NULL AND "group" = ANY(:g)'
        ),
        {"g": groups},
    ):
        if STRENGTH[row.shown] >= STRENGTH.get(audiences.get(row.widget, "available"), 0):
            audiences[row.widget] = row.shown
    found: list[dict[str, Any]] = []
    for row in rows:
        table = row.view_table
        if row.kind != "text":
            known = records.catalog.table(table) if table else None
            if known is None or known.switched_off or not records.access.of(known.key, "read"):
                continue
        own = row.dashboard == dashboard
        shown: str = (audiences.get(row.name) or str(row.shown)) if own else "available"
        found.append(_definition(row, table, records.situation.language, shown))
    return found


async def dashboard(records: Records, name: str) -> dict[str, Any]:
    """A dashboard as the person sees it: the widgets offered, and their arrangement (their own,
    else the defaults); widgets they must see are always in it."""
    offered = await _offered(records, name)
    by_name = {w["name"]: w for w in offered}
    await switch_role(records.conn, "sys")
    layout = (
        await records.conn.execute(
            text(
                "SELECT items, known FROM sys.dashboard_layout"
                " WHERE person = :p AND dashboard = :d AND deleted IS NULL"
            ),
            {"p": records.situation.person, "d": name},
        )
    ).first()
    if layout is None:
        items = [
            {"widget": w["name"], "size": w["size"]}
            for w in offered
            if w["shown"] in ("default", "mandatory")
        ]
        known: set[str] = set(by_name)
    else:
        items = [
            {
                "widget": i["widget"],
                "size": i["size"]
                if i["size"] in by_name[i["widget"]]["sizes"]
                else by_name[i["widget"]]["size"],
            }
            | ({"params": i["params"]} if i.get("params") else {})
            for i in cast(list[dict[str, Any]], layout.items)
            if i.get("widget") in by_name
        ]
        known = set(cast(list[str], layout.known or []))
    placed = {i["widget"] for i in items}
    items += [
        {"widget": w["name"], "size": w["size"]}
        for w in offered
        if w["shown"] == "mandatory" and w["name"] not in placed
    ]
    fresh = [w["name"] for w in offered if w["name"] not in known and w["name"] not in placed]
    return {"dashboard": name, "widgets": offered, "items": items, "new": fresh}


# ---- what people save ----


def _invalid(name: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(name, "invalid", message)],
    )


def _params_fit(params: Any) -> bool:
    """A placement's own choices: none, or a few names, each a record's ID."""
    if params is None:
        return True
    if not isinstance(params, dict) or len(cast(dict[Any, Any], params)) > MAX_PARAMS:
        return False
    return all(
        isinstance(k, str) and PARAM.match(k) and isinstance(v, str) and ID.match(v)
        for k, v in cast(dict[Any, Any], params).items()
    )


async def _check_layout(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["person"] = change.person
    items: Any = change.values.get("items")
    if items is None:
        return
    if not isinstance(items, list) or len(cast(list[Any], items)) > MAX_ITEMS:
        raise _invalid("items", f"A list of at most {MAX_ITEMS} widgets.")
    for item in cast(list[Any], items):
        if (
            not isinstance(item, dict)
            or not isinstance(cast(dict[str, Any], item).get("widget"), str)
            or cast(dict[str, Any], item).get("size") not in SIZES
            or set(cast(dict[str, Any], item)) - {"widget", "size", "params"}
        ):
            raise _invalid(
                "items", 'Each is {"widget": name, "size": "s", "wide" or "large", "params": {…}}.'
            )
        if not _params_fit(cast(dict[str, Any], item).get("params")):
            raise _invalid("items", 'A widget\'s choices name a record each: {"plan": its ID}.')


TABLE_HOOKS = (TableHook("sys.dashboard_layout", before=_check_layout),)
