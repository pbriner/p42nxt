"""The search across lists: each searchable list view, searched in its fields, as the person.

Every list is read through the records service, so the person's access applies as it does in the
list itself: a record they may not see is never found, nor counted.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from p42cloud.data.catalog import TableDef
from p42cloud.data.presenting import annotate, translated
from p42cloud.data.records import Records
from p42cloud.kernel.errors import Problem

MIN_LENGTH = 2
MAX_LENGTH = 100
MAX_SCOPES = 60
SUBTITLE_COLUMNS = 3


@dataclass(frozen=True)
class Scope:
    name: str
    label: str
    module: str
    module_label: str
    table: TableDef
    columns: list[str]
    search: list[str]
    filter: Any


async def scopes(records: Records) -> list[Scope]:
    """The lists the person may search, in the order of their modules."""
    language = records.situation.language
    rows = await records.conn.execute(
        text(
            'SELECT v.name, v.label, v."table", v.columns, v.search, v.filter, v.module,'
            " m.label AS module_label FROM sys.list_view v JOIN sys.module m ON m.name = v.module"
            " WHERE v.deleted IS NULL AND v.searchable AND v.search IS NOT NULL"
            " AND m.deleted IS NULL AND m.enabled ORDER BY m.label, v.name"
        )
    )
    found: list[Scope] = []
    for row in rows:
        table = records.catalog.table(row.table)
        if table is None or table.retired or table.switched_off:
            continue
        if not records.access.of(table.key, "read"):
            continue
        found.append(
            Scope(
                row.name,
                str(translated(row.label, language)),
                row.module,
                row.module_label,
                table,
                list(row.columns),
                list(row.search),
                row.filter,
            )
        )
    return found[:MAX_SCOPES]


def _shown(record: dict[str, Any], name: str) -> str:
    value = record.get("_display", {}).get(name, record.get(name))
    return "" if value is None else str(value)


def _hit(scope: Scope, record: dict[str, Any]) -> dict[str, Any]:
    """A found record as a line: its name and first column, then a few more."""
    display = scope.table.display
    others = [c for c in scope.columns if c != display]
    title = [_shown(record, n) for n in ([display] if display else []) + others[:1]]
    rest = [_shown(record, n) for n in others[1 : 1 + SUBTITLE_COLUMNS]]
    return {
        "sys_id": record["sys_id"],
        "title": " · ".join(t for t in title if t) or str(record["sys_id"]),
        "subtitle": " · ".join(s for s in rest if s),
    }


async def search(
    records: Records, words: str, *, scope: str | None = None, per_list: int = 5
) -> dict[str, Any]:
    """The records matching `words` in each list (or one list), with each list's total."""
    words = " ".join(words.split())
    if not MIN_LENGTH <= len(words) <= MAX_LENGTH:
        raise Problem(422, "validation", f"Search for {MIN_LENGTH} to {MAX_LENGTH} characters.")
    lists = [s for s in await scopes(records) if scope is None or s.name == scope]
    if scope is not None and not lists:
        raise Problem(404, "no_scope", "No such list to search in.")
    groups: list[dict[str, Any]] = []
    for found in lists:
        matching: Any = {
            "or": [{"field": f, "op": "contains", "value": words} for f in found.search]
        }
        where = matching if found.filter is None else {"and": [found.filter, matching]}
        try:
            page = await records.list(found.table.key, where=where, limit=per_list, count=True)
        except Problem:
            continue  # a field of the list the person may not read: nothing to find there
        if not page.records:
            continue
        shown = await annotate(records, found.table, page.records)
        groups.append(
            {
                "list": found.name,
                "label": found.label,
                "module": found.module,
                "module_label": found.module_label,
                "table": found.table.key,
                "total": page.total,
                "total_exact": page.total_exact,
                "records": [_hit(found, r) for r in shown],
            }
        )
    return {"query": words, "groups": groups}
