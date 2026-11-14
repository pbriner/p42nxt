"""Totals over a table: a count, sum, average, smallest or largest value, overall or per group
(a field's value, or a day, week, month or year of a date), for widgets and charts.

The person's access applies as in lists: only records they may read count, and a field they may
not read on a record counts as empty there.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from p42cloud.data.access import Rules, forbidden
from p42cloud.data.catalog import FieldDef, TableDef
from p42cloud.data.conditions import Filter, Query, column, field_of
from p42cloud.data.presenting import display_names, translated
from p42cloud.data.records import Records
from p42cloud.data.sql import qualified
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem

MEASURES = frozenset({"count", "sum", "avg", "min", "max"})
PERIODS = frozenset({"day", "week", "month", "year"})
GROUPED_KINDS = frozenset({"choice", "reference", "boolean", "text", "date", "datetime"})
MAX_GROUPS = 50


@dataclass(frozen=True)
class Totals:
    total: Any
    groups: list[dict[str, Any]]
    other: Any  # what the groups past the limit add up to (count and sum only)


def _invalid(name: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(name, "invalid", message)],
    )


def _readable(table: TableDef, rules: Rules, f: FieldDef, expression: str) -> str:
    allowed = rules.field(table, "t", f.name)
    return expression if allowed == "true" else f"(CASE WHEN {allowed} THEN {expression} END)"


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


class _Builder:
    def __init__(self, records: Records, table: TableDef, query: Query) -> None:
        self.records = records
        self.table = table
        self.query = query
        self.rules = Rules(records.catalog, records.situation, records.access, query)

    def measured(self, measure: str, name: str | None) -> str:
        if measure not in MEASURES:
            raise _invalid("measure", f"One of {', '.join(sorted(MEASURES))}.")
        if measure == "count":
            return "count(*)"
        f = field_of(self.table, name or "")
        if f is None or f.type.kind != "number":
            raise _invalid("field", "A number field to add up.")
        return f"{measure}({_readable(self.table, self.rules, f, f't.{f.name}')})"

    def grouped(self, name: str, period: str | None) -> tuple[FieldDef, str]:
        f = field_of(self.table, name)
        if f is None or f.type.kind not in GROUPED_KINDS:
            raise _invalid(
                "group", "A field to group by: a choice, reference, yes/no, text or date."
            )
        expression = column("t", f, self.query, self.records.situation)
        if f.type.kind in ("date", "datetime"):
            if period not in PERIODS:
                raise _invalid("period", f"Dates group by one of {', '.join(sorted(PERIODS))}.")
            zone = self.query.bind(str(self.records.situation.zone))
            local = expression if f.type.kind == "date" else f"({expression} AT TIME ZONE {zone})"
            expression = f"date_trunc({self.query.bind(period)}, {local})::date"
        return f, _readable(self.table, self.rules, f, expression)


async def _labels(records: Records, f: FieldDef, keys: list[Any]) -> dict[Any, Any]:
    language = records.situation.language
    if f.field.type == "choice":
        return {v["value"]: translated(v.get("label"), language) for v in f.field.options["values"]}
    target = f.type.references(f.field.options)
    if target is not None:
        return await display_names(records, records.catalog, target, {k for k in keys if k})
    return {}


async def aggregate(  # noqa: PLR0913 - what to total and how, all by keyword
    records: Records,
    key: str,
    *,
    where: Any = None,
    measure: str = "count",
    field: str | None = None,
    group: str | None = None,
    period: str | None = None,
    limit: int = 10,
) -> Totals:
    table = records.catalog.table(key)
    if table is None or table.switched_off:
        raise Problem(404, "no_table", "There's no such table.", detail=key)
    if not records.access.of(table.key, "read"):
        raise forbidden(table.key)
    if not 1 <= limit <= MAX_GROUPS:
        raise _invalid("limit", f"From 1 to {MAX_GROUPS} groups.")
    await switch_role(records.conn, table.schema)
    query = Query()
    build = _Builder(records, table, query)
    value = build.measured(measure, field)
    conditions = " AND ".join(
        [
            build.rules.rows(table, "t"),
            Filter(records.catalog, records.situation, query, guard=build.rules).sql(
                table, "t", where
            ),
            "t.deleted IS NULL",
        ]
    )
    source = f"FROM {qualified(table.schema, table.name)} t WHERE {conditions}"
    total = await records.conn.scalar(text(f"SELECT {value} {source}"), query.params)
    if group is None:
        return Totals(_json(total), [], None)
    f, expression = build.grouped(group, period)
    dated = f.type.kind in ("date", "datetime")
    order = "key ASC NULLS LAST" if dated else "value DESC NULLS LAST, key ASC NULLS LAST"
    rows = (
        await records.conn.execute(
            text(
                f"SELECT {expression} AS key, {value} AS value {source}"
                f" GROUP BY 1 ORDER BY {order} LIMIT {query.bind(limit + 1)}"
            ),
            query.params,
        )
    ).all()
    shown = rows[:limit]
    labels = await _labels(records, f, [r.key for r in shown])
    groups = [
        {"key": _json(r.key), "label": labels.get(r.key, _json(r.key)), "value": _json(r.value)}
        for r in shown
    ]
    other = None
    if len(rows) > limit and measure in ("count", "sum") and total is not None:
        other = _json(total - sum(r.value or 0 for r in shown))
    return Totals(_json(total), groups, other)
