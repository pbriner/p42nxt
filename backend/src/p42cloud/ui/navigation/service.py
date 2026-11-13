"""Writing a module's navigation into records, and the navigator as one person may see it."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.conditions import Filter, Query, Situation
from p42cloud.data.presenting import translated
from p42cloud.data.records import Records
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import label_of, sync
from p42cloud.ui.navigation.models import ITEM_NAME, PAGE, Entry, Item, Section

ICON = r"^[a-z0-9_]{1,64}$"
MAX_KEYWORDS = 20
MAX_KEYWORD = 40
# The core's category comes last: people mostly work in the modules.
PLATFORM = "Platform"


def _flat(entries: tuple[Entry, ...]) -> list[tuple[Section | None, Item]]:
    return [
        (entry, item) if isinstance(entry, Section) else (None, entry)
        for entry in entries
        for item in (entry.items if isinstance(entry, Section) else (entry,))
    ]


def _item_problems(catalog: Catalog, views: set[str], item: Item) -> list[FieldError]:
    where = f"navigation.{item.name}"
    found: list[FieldError] = []
    if not ITEM_NAME.match(item.name):
        found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
    if item.kind == "page":
        if item.path is None or not PAGE.match(item.path):
            found.append(FieldError(where, "bad_path", "A page is a path in the app, like /help."))
    elif item.table is None:
        found.append(FieldError(where, "no_table", "A list or a new record needs its table."))
    table = catalog.table(item.table) if item.table else None
    if item.table and (table is None or table.retired):
        found.append(FieldError(where, "unknown_table", f"No table {item.table!r}."))
    if item.view is not None and item.view not in views:
        found.append(FieldError(where, "unknown_view", f"No list view {item.view!r}."))
    if item.filter is not None and table is not None:
        try:
            Filter(catalog, Situation(uuid.UUID(int=0)), Query()).sql(table, "t", item.filter)
        except Problem as problem:
            found += [FieldError(where, e.code, e.message) for e in problem.errors]
    if len(item.keywords) > MAX_KEYWORDS or any(len(k) > MAX_KEYWORD for k in item.keywords):
        found.append(FieldError(where, "bad_keywords", "At most 20 keywords of 40 characters."))
    return found


async def write_navigation(
    conn: AsyncConnection, catalog: Catalog, module: str, entries: tuple[Entry, ...]
) -> None:
    """Brings the module's navigation items to its declaration (after its list views)."""
    views = {
        str(n)
        for n in await conn.scalars(text("SELECT name FROM sys.list_view WHERE deleted IS NULL"))
    }
    flat = _flat(entries)
    found: list[FieldError] = []
    names: set[str] = set()
    for _, item in flat:
        found += _item_problems(catalog, views, item)
        if item.name in names:
            found.append(FieldError(f"navigation.{item.name}", "duplicate", "Declared twice."))
        names.add(item.name)
    if found:
        raise Problem(422, "invalid_navigation", "The navigation can't be used.", errors=found)
    rows = [
        {
            "name": f"{module}.{item.name}",
            "section": label_of(section.label) if section else None,
            "label": label_of(item.label),
            "icon": item.icon,
            "kind": item.kind,
            "table": item.table,
            "view": item.view,
            "filter": item.filter,
            "path": item.path,
            "keywords": list(item.keywords) or None,
            "position": position,
        }
        for position, (section, item) in enumerate(flat)
    ]
    await sync(
        conn,
        "sys.nav_item",
        module,
        rows,
        frozenset({"section", "label", "filter", "keywords"}),
    )


def _visible(records: Records, row: Any) -> bool:
    if row.table is None:
        return row.kind == "page"
    table = records.catalog.table(row.table)
    if table is None or table.retired or table.switched_off:
        return False
    return bool(records.access.of(table.key, "create" if row.kind == "create" else "read"))


async def navigation(records: Records) -> dict[str, Any]:
    """The navigator for the person: categories, their modules, sections and items.

    An item shows only to those who may open what it opens; a module without such items, or
    switched off, doesn't show.
    """
    language = records.situation.language
    rows = await records.conn.execute(
        text(
            'SELECT n.name, n.module, n.section, n.label, n.icon, n.kind, n."table", n.view,'
            " n.filter, n.path, n.keywords, m.label AS module_label, m.category,"
            " m.icon AS module_icon"
            " FROM sys.nav_item n JOIN sys.module m ON m.name = n.module"
            " WHERE n.deleted IS NULL AND m.deleted IS NULL AND m.enabled"
            " ORDER BY n.module, n.position, n.name"
        )
    )
    modules: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _visible(records, row):
            continue
        module = modules.setdefault(
            row.module,
            {
                "id": row.module,
                "name": row.module_label,
                "icon": row.module_icon,
                "category": row.category,
                "sections": [],
            },
        )
        section = translated(row.section, language)
        sections: list[dict[str, Any]] = module["sections"]
        if not sections or sections[-1]["label"] != section:
            sections.append({"label": section, "items": []})
        sections[-1]["items"].append(
            {
                "name": row.name,
                "label": translated(row.label, language),
                "icon": row.icon,
                "kind": row.kind,
                "table": row.table,
                "view": row.view,
                "filter": row.filter,
                "path": row.path,
                "keywords": row.keywords or [],
            }
        )
    ordered = sorted(
        modules.values(), key=lambda m: (m["category"] == PLATFORM, m["category"], m["name"])
    )
    categories: list[dict[str, Any]] = []
    for module in ordered:
        category = module.pop("category")
        if not categories or categories[-1]["name"] != category:
            categories.append({"name": category, "modules": []})
        categories[-1]["modules"].append(module)
    return {"categories": categories}
