"""A record's history: its entries of the audit log, as the person may see them.

Only someone who may read the record sees its history, and of each change only the fields they
may read (a secret's change was never logged with its values). Newest first. Each entry also
says how its values read: a reference's name (if the person may read it), a choice's label.
"""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import text

from p42cloud.data.catalog import FieldDef
from p42cloud.data.presenting import named, target_of, translated
from p42cloud.data.records import Records
from p42cloud.kernel.db import switch_role

MAX_ENTRIES = 200


async def history(
    records: Records, key: str, sys_id: UUID, limit: int = 100
) -> list[dict[str, Any]]:
    """The record's changes, newest first: when, what, by whom, through which channel."""
    record = await records.read(key, sys_id)  # not theirs to read: not found, as for the record
    readable = set(record.values)
    conn = records.conn
    await switch_role(conn, "sys")
    rows = list(
        await conn.execute(
            text(
                "SELECT created, operation, person_id, channel, changes FROM sys.audit_log"
                " WHERE table_name = :t AND record_id = :id ORDER BY sys_id DESC LIMIT :n"
            ),
            {"t": key, "id": sys_id, "n": max(1, min(limit, MAX_ENTRIES))},
        )
    )
    people = {
        row.sys_id: row.name
        for row in await conn.execute(
            text("SELECT sys_id, name FROM sys.person WHERE sys_id = ANY(:ids)"),
            {"ids": list({r.person_id for r in rows})},
        )
    }
    entries = [
        {
            "at": r.created.isoformat(),
            "operation": r.operation,
            "by": str(r.person_id),
            "by_name": people.get(r.person_id),
            "channel": r.channel,
            "changes": {k: v for k, v in dict(r.changes).items() if k in readable},
        }
        for r in rows
    ]
    await _display(records, key, record, entries)
    return entries


def _sides(value: Any) -> list[Any]:
    """A change's before and after (a new record's values have no before)."""
    if isinstance(value, list):
        pair = cast(list[Any], value)
        return pair
    return [None, value]


async def _display(records: Records, key: str, record: Any, entries: list[dict[str, Any]]) -> None:
    """Adds `display` to each entry: per field, how its before and after read, for references
    (their names, if readable) and choices (their labels)."""
    table = records.catalog.tables[key]
    fields: dict[str, FieldDef] = {f.name: f for f in table.fields}
    targets = {
        name: target
        for name, f in fields.items()
        if (target := target_of(records.catalog, table, f, record)) is not None
    }
    wanted: dict[str, set[UUID]] = {}
    for entry in entries:
        for name, value in entry["changes"].items():
            if name in targets:
                wanted.setdefault(targets[name], set()).update(_ids(value))
    names = {t: await named(records, records.catalog, t, ids) for t, ids in wanted.items()}
    language = records.situation.language
    for entry in entries:
        shown: dict[str, list[Any]] = {}
        for name, value in entry["changes"].items():
            if name in targets:
                found = names.get(targets[name], {})
                shown[name] = [_name(found, side) for side in _sides(value)]
            elif (f := fields.get(name)) is not None and f.field.type == "choice":
                shown[name] = [_label(f, side, language) for side in _sides(value)]
        if shown:
            entry["display"] = shown


def _ids(value: Any) -> set[UUID]:
    found: set[UUID] = set()
    for side in _sides(value):
        try:
            found.add(UUID(str(side)))
        except ValueError:
            continue
    return found


def _label(f: FieldDef, value: Any, language: str) -> Any:
    for entry in f.field.options.get("values", []):
        if entry["value"] == value:
            return translated(entry["label"], language)
    return value


def _name(found: dict[UUID, tuple[Any, bool]], side: Any) -> Any:
    if side is None:
        return None
    try:
        hit = found.get(UUID(str(side)))
    except ValueError:
        return side
    return hit[0] if hit is not None else None
