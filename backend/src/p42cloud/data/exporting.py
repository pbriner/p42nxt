"""Lists as files: CSV, Excel or JSON, with the list's filter, columns and sort, and only what the
person may read. References and choices come named, as the list shows them; Excel gets real
numbers and dates, so totals and sorting work there. Every export is recorded in the audit log.
"""

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import xlsxwriter  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import text

from p42cloud.data.catalog import FieldDef, TableDef
from p42cloud.data.conditions import field_of
from p42cloud.data.presenting import annotate, translated
from p42cloud.data.records import Records
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem

MAX_ROWS = 50_000
PAGE = 500
FORMATS = {
    "csv": ("text/csv; charset=utf-8", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "json": ("application/json", "json"),
}
_NUMBERS = frozenset({"integer", "decimal", "money", "percent", "formula", "rollup"})


@dataclass(frozen=True)
class Column:
    name: str  # a field, or a path through references (`partner.city`)
    label: str
    field: FieldDef


@dataclass(frozen=True)
class Exported:
    content: bytes
    media_type: str
    filename: str
    rows: int


def _readable(records: Records, table: TableDef, name: str) -> bool:
    return any(p.fields is None or name in p.fields for p in records.access.of(table.key, "read"))


def _columns(records: Records, table: TableDef, wanted: list[str]) -> list[Column]:
    """The columns asked for, or every field the person may read; labels in their language."""
    language = records.situation.language
    names = wanted or [
        f.name
        for f in table.active_fields()
        if not f.type.secret and _readable(records, table, f.name)
    ]
    found: list[Column] = []
    for name in names:
        labels: list[str] = []
        current: TableDef | None = table
        f: FieldDef | None = None
        for part in name.split("."):
            f = field_of(current, part) if current is not None else None
            if f is None:
                raise Problem(422, "unknown_field", f"There's no column {name!r} to export.")
            labels.append(translated(f.field.label, language) or part)
            target = f.type.references(f.field.options)
            current = records.catalog.table(target) if target else None
        if f is not None:
            found.append(Column(name, " > ".join(labels), f))
    return found


def _value(column: Column, body: dict[str, Any]) -> Any:
    """What the list shows: a reference's or choice's name, else the value as JSON gives it."""
    shown = body.get("_display", {}).get(column.name)
    if shown is not None:
        return shown
    return body.get(column.name)


def _typed(column: Column, value: Any) -> Any:  # noqa: PLR0911 - one return per kind
    """For Excel: numbers as numbers, dates as dates; the rest as text."""
    if value is None or value == "":
        return None
    kind = column.field.type.name
    if kind in _NUMBERS and isinstance(value, str | int | float):
        return float(Decimal(str(value)))
    if kind == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if kind == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)
    if isinstance(value, bool):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _csv(columns: list[Column], bodies: list[dict[str, Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([c.label for c in columns])
    for body in bodies:
        row: list[Any] = []
        for c in columns:
            value = _value(c, body)
            if isinstance(value, dict | list):
                value = json.dumps(value, ensure_ascii=False)
            row.append("" if value is None else value)
        writer.writerow(row)
    # The byte order mark tells Excel it's UTF-8 (umlauts and accents stay right).
    return ("﻿" + out.getvalue()).encode()


def _xlsx(title: str, columns: list[Column], bodies: list[dict[str, Any]]) -> bytes:
    out = io.BytesIO()
    book: Any = xlsxwriter.Workbook(out, {"in_memory": True, "default_date_format": "yyyy-mm-dd"})
    sheet: Any = book.add_worksheet(title[:31] or "Export")
    bold: Any = book.add_format({"bold": True})
    moment: Any = book.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    for x, c in enumerate(columns):
        sheet.write(0, x, c.label, bold)
        sheet.set_column(x, x, max(10, min(40, len(c.label) + 2)))
    for y, body in enumerate(bodies, start=1):
        for x, c in enumerate(columns):
            value = _typed(c, _value(c, body))
            if value is None:
                continue
            if isinstance(value, datetime):
                sheet.write_datetime(y, x, value, moment)
            elif isinstance(value, date):
                sheet.write_datetime(y, x, datetime(value.year, value.month, value.day))
            else:
                sheet.write(y, x, value)
    sheet.freeze_panes(1, 0)
    if columns:
        sheet.autofilter(0, 0, max(len(bodies), 1), len(columns) - 1)
    book.close()
    return out.getvalue()


def _json(key: str, columns: list[Column], bodies: list[dict[str, Any]]) -> bytes:
    kept = {c.name for c in columns} | {"sys_id"}
    records = [
        {k: v for k, v in body.items() if k in kept}
        | {"_display": {k: v for k, v in body.get("_display", {}).items() if k in kept}}
        for body in bodies
    ]
    exported = {"table": key, "exported": datetime.now(UTC).isoformat(), "records": records}
    return json.dumps(exported, ensure_ascii=False, indent=1).encode()


async def export(  # noqa: PLR0913 - the list's options, all by keyword
    records: Records,
    key: str,
    *,
    file_format: str,
    where: Any = None,
    sort: list[str] | None = None,
    fields: list[str] | None = None,
) -> Exported:
    """The list as a file; more than MAX_ROWS records are refused (narrow the filter)."""
    if file_format not in FORMATS:
        raise Problem(422, "bad_format", f"Export as {', '.join(FORMATS)}.")
    table = records.catalog.tables.get(key)
    if table is None:
        raise Problem(404, "not_found", "There's no such table.")
    columns = _columns(records, table, list(fields or []))
    paths = [c.name for c in columns if "." in c.name]
    bodies: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await records.list(
            key, where=where, sort=sort, limit=PAGE, after=after, count=False, columns=paths
        )
        bodies += await annotate(records, table, page.records)
        if len(bodies) > MAX_ROWS:
            raise Problem(
                422, "too_many", f"At most {MAX_ROWS:,} records are exported: narrow the filter."
            )
        after = page.next
        if after is None:
            break
    # Who took which records out, like a sign-in: an entry of the audit log.
    await switch_role(records.conn, "sys")
    details = {"table": key, "format": file_format, "rows": len(bodies), "filter": where}
    await records.conn.execute(
        text("SELECT sys.p42_security_event('data_exported', CAST(:details AS jsonb))"),
        {"details": json.dumps(details)},
    )
    media_type, extension = FORMATS[file_format]
    title = translated(table.label, records.situation.language) or table.name
    if file_format == "csv":
        content = _csv(columns, bodies)
    elif file_format == "xlsx":
        content = _xlsx(title, columns, bodies)
    else:
        content = _json(key, columns, bodies)
    day = datetime.now(UTC).date().isoformat()
    return Exported(content, media_type, f"{key.replace('.', '-')}-{day}.{extension}", len(bodies))
