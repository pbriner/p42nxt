"""The filter language: conditions as JSON, checked against the catalog and turned into SQL.

    {"and": [{"field": "state", "op": "in", "value": ["open", "waiting"]},
             {"not": {"field": "category.name", "op": "contains", "value": "desk"}}]}

Values are always bound parameters; table and field names come from the catalog, never from the
condition. Every condition is checked before anything runs, and anything unknown is refused,
never ignored. A single condition never evaluates to NULL, so `not` is an exact complement and
`ne` keeps records without a value.
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from p42cloud.data.catalog import Catalog, FieldDef, TableDef
from p42cloud.data.sql import ident, qualified
from p42cloud.data.types import Field, Invalid
from p42cloud.kernel.errors import FieldError, Problem

MAX_CONDITIONS = 50
MAX_DEPTH = 4
MAX_WALK = 3
_BASE = frozenset({"eq", "ne", "empty", "not_empty"})
_ORDERED = _BASE | {"lt", "lte", "gt", "gte", "between"}
OPERATORS: dict[str, frozenset[str]] = {
    "text": _BASE | {"contains", "not_contains", "starts_with", "ends_with", "in", "not_in"},
    "translated": _BASE | {"contains", "not_contains", "starts_with", "ends_with"},
    "number": _ORDERED,
    "date": _ORDERED,
    "datetime": _ORDERED,
    "choice": _BASE | {"in", "not_in"},
    "reference": _BASE | {"in", "not_in"},
    "boolean": _BASE,
}
# Operators that also match records without a value (F-2), through references too.
KEEPS_EMPTY = frozenset({"ne", "not_in", "not_contains", "empty"})
_LIST_OPERATORS = frozenset({"in", "not_in"})

SYSTEM_FIELDS: dict[str, Field] = {
    "sys_id": Field("reference", {"en": "ID"}, {"table": "-"}),
    "created": Field("datetime", {"en": "Created"}),
    "created_by": Field("reference", {"en": "Created by"}, {"table": "sys.person"}),
    "updated": Field("datetime", {"en": "Updated"}),
    "updated_by": Field("reference", {"en": "Updated by"}, {"table": "sys.person"}),
    "source": Field("string", {"en": "Source"}, {"max_length": 255}),
    "deleted": Field("datetime", {"en": "Deleted"}),
    "deleted_by": Field("reference", {"en": "Deleted by"}, {"table": "sys.person"}),
}


@dataclass(frozen=True)
class Situation:
    """Who a condition is evaluated for, and when: fills `@me`, `@today` and the language."""

    person: UUID
    language: str = "en"
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    zone: ZoneInfo = field(default_factory=lambda: ZoneInfo("UTC"))

    @property
    def today(self) -> date:
        return self.now.astimezone(self.zone).date()


class Query:
    """The parameters and table aliases of one statement being built."""

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self._aliases = 0

    def bind(self, value: Any) -> str:
        name = f"p{len(self.params)}"
        self.params[name] = value
        return f":{name}"

    def alias(self) -> str:
        self._aliases += 1
        return f"t{self._aliases}"


def refuse(where: str, code: str, message: str) -> Problem:
    return Problem(
        422,
        "invalid_condition",
        "The filter can't be used.",
        errors=[FieldError(where, code, message)],
    )


def field_of(table: TableDef, name: str) -> FieldDef | None:
    """An active field or a system field of the table; retired fields aren't found."""
    if name in SYSTEM_FIELDS:
        return FieldDef(name, SYSTEM_FIELDS[name], -1)
    return next((f for f in table.active_fields() if f.name == name), None)


def column(alias: str, f: FieldDef, query: Query, situation: Situation) -> str:
    """The SQL for a field's value; a translated text in the person's language, else English."""
    plain = f"{alias}.{ident(f.name)}"
    if f.type.kind == "translated":
        return f"coalesce({plain} ->> {query.bind(situation.language)}, {plain} ->> 'en')"
    return plain


# ---- values ----

_RELATIVE = re.compile(r"^@(today|now)(?:([+-])(\d{1,4})([dwmyh]))?$")
_PERIOD = re.compile(r"^@(start|end)_of_(week|month|quarter|year)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _add_months(day: date, months: int) -> date:
    """The same day `months` later, or the month's last day if it has fewer (31 Jan + 1m)."""
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def _period(kind: str, edge: str, today: date) -> date:
    if kind == "week":
        start = today - timedelta(days=today.weekday())
        return start if edge == "start" else start + timedelta(days=6)
    months = {"month": 1, "quarter": 3, "year": 12}[kind]
    first_month = (today.month - 1) // months * months + 1 if kind != "year" else 1
    start = date(today.year, first_month, 1)
    return start if edge == "start" else _add_months(start, months) - timedelta(days=1)


def _relative(found: re.Match[str], situation: Situation) -> date | datetime:
    base, sign, amount, unit = found.groups()
    n = int(amount or 0) * (-1 if sign == "-" else 1)
    months = n * (12 if unit == "y" else 1) if unit in ("m", "y") else 0
    if base == "today":
        if unit == "h":
            raise Invalid("@today moves by days, weeks, months or years; use @now for hours.")
        if months:
            return _add_months(situation.today, months)
        return situation.today + timedelta(days=n * (7 if unit == "w" else 1))
    if months:
        local = situation.now.astimezone(situation.zone)
        return datetime.combine(_add_months(local.date(), months), local.timetz())
    return situation.now + timedelta(hours=n * {"h": 1, "d": 24, "w": 168}.get(unit or "h", 1))


def _iso(value: str) -> date | datetime:
    try:
        if _DATE.match(value):
            return date.fromisoformat(value)
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise Invalid("Must be a date, a date and time, or a variable like @today.") from error
    if parsed.tzinfo is None:
        raise Invalid("A date and time needs a time zone.")
    return parsed


def moment(value: Any, situation: Situation) -> date | datetime:
    """A date or time from a condition: ISO text or a variable (`@today-7d`, `@start_of_month`)."""
    if not isinstance(value, str):
        raise Invalid("Must be a date, a date and time, or a variable like @today.")
    if found := _RELATIVE.match(value):
        return _relative(found, situation)
    if found := _PERIOD.match(value):
        return _period(found[2], found[1], situation.today)
    return _iso(value)


def _day(day: date, situation: Situation) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(), situation.zone)
    return start, datetime.combine(day + timedelta(days=1), time(), situation.zone)


def value_of(f: FieldDef, raw: Any, situation: Situation) -> Any:
    """One value of a condition, checked by the field's type (variables resolved)."""
    kind = f.type.kind
    if kind in ("text", "translated"):
        if not isinstance(raw, str):
            raise Invalid("Must be text.")
        return raw
    if kind == "reference" and raw == "@me":
        return situation.person
    if kind == "date":
        found = moment(raw, situation)
        return found.astimezone(situation.zone).date() if isinstance(found, datetime) else found
    if kind == "datetime":
        return moment(raw, situation)
    return f.type.check(raw, f.field.options)


# ---- building ----


@dataclass(frozen=True)
class _Leaf:
    path: str
    op: str
    value: Any


@dataclass
class Filter:
    """Builds the SQL for conditions on tables of one catalog, into one query."""

    catalog: Catalog
    situation: Situation
    query: Query
    conditions: int = 0

    def sql(self, table: TableDef, alias: str, condition: Any) -> str:
        """The SQL for a condition on `table` (as `alias`); None or {} matches everything."""
        if condition is None or condition == {}:
            return "true"
        return self.build(table, alias, condition)

    def build(self, table: TableDef, alias: str, node: Any, depth: int = 0) -> str:
        if depth > MAX_DEPTH:
            raise refuse("", "too_deep", f"Groups may be nested at most {MAX_DEPTH} deep.")
        if not isinstance(node, dict):
            raise refuse("", "bad_shape", "A condition or a group (and, or, not) was expected.")
        group = cast(dict[str, Any], node)
        if set(group) == {"and"} or set(group) == {"or"}:
            joiner, empty = ("AND", "true") if "and" in group else ("OR", "false")
            parts = group.get("and", group.get("or"))
            if not isinstance(parts, list):
                raise refuse("", "bad_shape", f"`{joiner.lower()}` takes a list.")
            built = [self.build(table, alias, p, depth + 1) for p in cast(list[Any], parts)]
            return f"({f' {joiner} '.join(built)})" if built else empty
        if set(group) == {"not"}:
            return f"(NOT {self.build(table, alias, group['not'], depth + 1)})"
        return self.leaf(table, alias, group)

    def leaf(self, table: TableDef, alias: str, node: dict[str, Any]) -> str:
        self.conditions += 1
        if self.conditions > MAX_CONDITIONS:
            raise refuse("", "too_many", f"At most {MAX_CONDITIONS} conditions.")
        path, op = node.get("field"), node.get("op")
        if (
            set(node) - {"field", "op", "value"}
            or not isinstance(path, str)
            or not isinstance(op, str)
        ):
            raise refuse(str(path), "bad_shape", "A condition has a field, an op and a value.")
        names = path.split(".")
        if len(names) > MAX_WALK + 1:
            raise refuse(path, "too_far", f"At most {MAX_WALK} references can be followed.")
        return self.walk(table, alias, names, _Leaf(path, op, node.get("value")))

    def walk(self, table: TableDef, alias: str, names: list[str], leaf: _Leaf) -> str:
        path, op = leaf.path, leaf.op
        f = field_of(table, names[0])
        if f is None:
            raise refuse(path, "unknown_field", f"{table.key} has no field {names[0]!r}.")
        if len(names) == 1:
            return f"COALESCE({self.compare(alias, f, leaf)}, false)"
        target_key = f.type.references(f.field.options)
        target = self.catalog.table(target_key) if target_key else None
        if target is None or target.retired:
            raise refuse(path, "not_a_reference", f"{names[0]!r} can't be followed.")
        inner = self.query.alias()
        condition = self.walk(target, inner, names[1:], leaf)
        ref = f"{alias}.{ident(f.name)}"
        source = qualified(target.schema, target.name)
        # Names from the catalog, checked by ident(); values are bound in `condition`.
        found = f"{ref} IN (SELECT {inner}.sys_id FROM {source} {inner} WHERE {condition})"  # noqa: S608
        return f"({ref} IS NULL OR {found})" if op in KEEPS_EMPTY else f"COALESCE({found}, false)"

    def compare(self, alias: str, f: FieldDef, leaf: _Leaf) -> str:
        path, op, raw = leaf.path, leaf.op, leaf.value
        kind = f.type.kind
        if kind is None:
            raise refuse(path, "not_filterable", f"{f.name!r} can't be filtered.")
        if op not in OPERATORS[kind]:
            ops = ", ".join(sorted(OPERATORS[kind]))
            raise refuse(path, "bad_operator", f"{op!r} doesn't work here; use one of {ops}.")
        x = column(alias, f, self.query, self.situation)
        if op in ("empty", "not_empty"):
            blank = (
                f"({x} IS NULL OR {x} = '')" if kind in ("text", "translated") else f"{x} IS NULL"
            )
            return blank if op == "empty" else f"(NOT {blank})"
        try:
            if kind in ("text", "translated"):
                return self.text(x, f, op, raw)
            if kind == "datetime":
                return self.moment(x, op, raw)
            return self.plain(x, f, op, raw)
        except Invalid as error:
            raise refuse(path, "bad_value", str(error)) from error

    def values(self, f: FieldDef, op: str, raw: Any) -> list[Any]:
        if op in _LIST_OPERATORS or op == "between":
            if not isinstance(raw, list) or not raw:
                raise Invalid("Needs a list of values.")
            items = cast(list[Any], raw)
            if op == "between" and len(items) != 2:  # noqa: PLR2004
                raise Invalid("`between` needs two values: from and to.")
            return [value_of(f, item, self.situation) for item in items]
        return [value_of(f, raw, self.situation)]

    def text(self, x: str, f: FieldDef, op: str, raw: Any) -> str:
        def norm(sql: str) -> str:
            return f"sys.unaccent('sys.unaccent'::regdictionary, lower({sql}))"

        values = self.values(f, op, raw)
        if op in _LIST_OPERATORS:
            listed = ", ".join(norm(self.query.bind(v)) for v in values)
            found = f"{norm(x)} IN ({listed})"
            return f"({x} IS NULL OR NOT {found})" if op == "not_in" else found
        if op in ("eq", "ne"):
            same = f"{norm(x)} = {norm(self.query.bind(values[0]))}"
            return same if op == "eq" else f"({x} IS NULL OR NOT {same})"
        escaped = re.sub(r"([\\%_])", r"\\\1", values[0])
        pattern = {
            "contains": f"%{escaped}%",
            "not_contains": f"%{escaped}%",
            "starts_with": f"{escaped}%",
            "ends_with": f"%{escaped}",
        }[op]
        like = f"{norm(x)} LIKE {norm(self.query.bind(pattern))} ESCAPE '\\'"
        return f"({x} IS NULL OR NOT {like})" if op == "not_contains" else like

    def plain(self, x: str, f: FieldDef, op: str, raw: Any) -> str:
        values = [self.query.bind(v) for v in self.values(f, op, raw)]
        if op == "between":
            return f"({x} >= {values[0]} AND {x} <= {values[1]})"
        if op in _LIST_OPERATORS:
            found = f"{x} IN ({', '.join(values)})"
            return f"({x} IS NULL OR NOT {found})" if op == "not_in" else found
        sign = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[op]
        compared = f"{x} {sign} {values[0]}"
        return f"({x} IS NULL OR {compared})" if op == "ne" else compared

    def moment(self, x: str, op: str, raw: Any) -> str:
        """A time compared with a date means the whole local day (`eq @today`)."""
        items = cast(list[Any], raw) if op == "between" and isinstance(raw, list) else [raw]
        if op == "between" and len(items) != 2:  # noqa: PLR2004
            raise Invalid("`between` needs two values: from and to.")
        found = [moment(item, self.situation) for item in items]

        def bounds(value: date | datetime) -> tuple[str, str, bool]:
            """Start and end of the value, and whether the end is excluded (a whole day)."""
            if isinstance(value, datetime):
                bound = self.query.bind(value)
                return bound, bound, False
            start, end = _day(value, self.situation)
            return self.query.bind(start), self.query.bind(end), True

        first_start, first_end, whole = bounds(found[0])
        before_end = "<" if whole else "<="
        if op == "between":
            _, last_end, last_whole = bounds(found[1])
            return f"({x} >= {first_start} AND {x} {'<' if last_whole else '<='} {last_end})"
        within = f"({x} >= {first_start} AND {x} {before_end} {first_end})"
        return {
            "eq": within,
            "ne": f"({x} IS NULL OR NOT {within})",
            "lt": f"{x} < {first_start}",
            "lte": f"{x} {before_end} {first_end}",
            "gt": f"{x} >= {first_end}" if whole else f"{x} > {first_end}",
            "gte": f"{x} >= {first_start}",
        }[op]
