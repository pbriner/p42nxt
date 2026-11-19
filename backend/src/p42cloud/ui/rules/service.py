"""Writing a module's form rules into records, and enforcing them on every save.

The browser applies the rules while the person types; the server applies them again, whatever
the browser did: a field a rule locks may not change, and one it requires may not stay empty.
"""

import json
import re
import uuid
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import Filter, Query, Situation, field_of, is_dynamic, value_of
from p42cloud.data.hooks import EVERY_TABLE, Change, TableHook
from p42cloud.data.sql import ident, qualified
from p42cloud.data.types import Invalid
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import sync
from p42cloud.ui.rules.models import FILL_OPERATORS, RULE_OPERATORS, RULE_VARIABLES, FormRule

RULE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _subset_problems(
    node: Any, table: TableDef, operators: frozenset[str] = RULE_OPERATORS
) -> list[str]:
    """What in a condition the browser couldn't evaluate the same way (`operators`: those of a
    rule the browser applies, or of one only the server applies)."""
    if node is None:
        return []
    if not isinstance(node, dict):
        return ["A condition or a group (and, or, not) was expected."]
    group = cast(dict[str, Any], node)
    if "and" in group or "or" in group:
        parts = group.get("and", group.get("or"))
        if not isinstance(parts, list):
            return ["A group lists its conditions."]
        return [
            p for part in cast(list[Any], parts) for p in _subset_problems(part, table, operators)
        ]
    if "not" in group:
        return _subset_problems(group["not"], table, operators)
    name = group.get("field")
    found: list[str] = []
    if not isinstance(name, str) or "." in name or field_of(table, name) is None:
        found.append(f"Rules use the table's own fields, not {name!r}.")
    if group.get("op", "eq") not in operators:
        found.append(f"Rules can't use {group.get('op')!r}.")
    values: list[Any] = (
        cast(list[Any], group.get("value"))
        if isinstance(group.get("value"), list)
        else [group.get("value")]
    )
    found += [
        f"Rules can't use {v!r}; only {', '.join(sorted(RULE_VARIABLES))}."
        for v in values
        if isinstance(v, str) and v.startswith("@") and v not in RULE_VARIABLES
    ]
    return found


def _problems(catalog: Catalog, module: str, rule: FormRule) -> list[FieldError]:
    where = f"rules.{rule.name}"
    table = catalog.table(rule.table)
    if table is None or table.retired or table.schema != module:
        return [FieldError(where, "unknown_table", "A rule is about one of the module's tables.")]
    found: list[FieldError] = []
    if not RULE_NAME.match(rule.name):
        found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
    for name in (*rule.hide, *rule.required, *rule.read_only):
        if field_of(table, name) is None:
            found.append(FieldError(where, "unknown_field", f"No field {name!r}."))
    found += [FieldError(where, "bad_fill", m) for m in fill_problems(table, rule.fill)]
    # Only the server fills: a rule that does nothing else may use patterns too.
    only_fills = bool(rule.fill) and not (rule.hide or rule.required or rule.read_only)
    operators = FILL_OPERATORS if only_fills else RULE_OPERATORS
    found += [
        FieldError(where, "not_for_rules", m)
        for m in _subset_problems(rule.condition, table, operators)
    ]
    if rule.condition is not None and not found:
        try:
            Filter(catalog, Situation(uuid.UUID(int=0)), Query()).sql(table, "t", rule.condition)
        except Problem as problem:
            found += [FieldError(where, e.code, e.message) for e in problem.errors]
    return found


def fill_problems(table: TableDef, fill: Any) -> list[str]:
    """What's wrong with a rule's fields to fill: each one of the table's own fields that
    people set, with a value it takes (or a word like @me, @today+14d)."""
    if fill is None:
        return []
    if not isinstance(fill, dict) or not fill:
        return ["Fill names fields and their values."]
    found: list[str] = []
    for name, value in cast(dict[str, Any], fill).items():
        f = next((f for f in table.active_fields() if f.name == name), None)
        if f is None or f.type.generated:
            found.append(f"{name!r} isn't a field a rule can fill.")
            continue
        try:
            if is_dynamic(f, value):
                value_of(f, value, Situation(uuid.UUID(int=0)))
            else:
                f.type.check(value, f.field.options)
        except Invalid as error:
            found.append(f"{name}: {error}")
    return found


async def write_rules(
    conn: AsyncConnection, catalog: Catalog, module: str, rules: tuple[FormRule, ...]
) -> None:
    found = [p for r in rules for p in _problems(catalog, module, r)]
    if len({r.name for r in rules}) != len(rules):
        found.append(FieldError("rules", "duplicate", "A rule's name is used twice."))
    if found:
        raise Problem(422, "invalid_rules", "The form rules can't be used.", errors=found)
    rows = [
        {
            "name": f"{module}.{r.name}",
            "table": r.table,
            "condition": r.condition,
            "hide": list(r.hide) or None,
            "required": list(r.required) or None,
            "read_only": list(r.read_only) or None,
            "fill": r.fill or None,
        }
        for r in rules
    ]
    await sync(
        conn,
        "sys.form_rule",
        module,
        rows,
        frozenset({"condition", "hide", "required", "read_only", "fill"}),
    )


# ---- enforcing ----


async def _rules(conn: AsyncConnection, table: str, effect: str) -> list[Any]:
    await switch_role(conn, "sys")
    return list(
        await conn.execute(
            text(
                f"SELECT r.condition, r.{ident(effect)} AS fields FROM sys.form_rule r"  # noqa: S608
                " JOIN sys.module m ON m.name = r.module"
                f' WHERE r."table" = :t AND r.deleted IS NULL AND r.{ident(effect)} IS NOT NULL'
                " AND m.enabled AND m.deleted IS NULL"
            ),
            {"t": table},
        )
    )


async def _holds(change: Change, condition: Any) -> bool:
    """Whether the condition holds for the record as stored now (every field counts)."""
    if condition is None:
        return True
    records = change.records
    table = change.table
    await switch_role(records.conn, table.schema)
    query = Query()
    sql = Filter(records.catalog, records.situation, query).sql(table, "t", condition)
    found = await records.conn.scalar(
        text(
            f"SELECT {sql} FROM {qualified(table.schema, table.name)} t"  # noqa: S608
            f" WHERE t.sys_id = {query.bind(change.sys_id)}"
        ),
        query.params,
    )
    return bool(found)


async def _stored(change: Change, names: list[str]) -> dict[str, Any]:
    table = change.table
    await switch_role(change.records.conn, table.schema)
    row = (
        await change.records.conn.execute(
            text(
                f"SELECT {', '.join(ident(n) for n in names)}"  # noqa: S608
                f" FROM {qualified(table.schema, table.name)} WHERE sys_id = :id"
            ),
            {"id": change.sys_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else {}  # pyright: ignore[reportPrivateUsage]


def _given(table: TableDef, name: str, value: Any) -> Any:
    """A value as sent, as it would be stored (text to dates, IDs …), to compare."""
    f = field_of(table, name)
    if f is None:
        return value
    try:
        return f.type.check(value, f.field.options)
    except Invalid:
        return value  # refused later, as invalid


def _refuse(errors: list[FieldError]) -> Problem:
    return Problem(422, "validation", "Some fields aren't valid.", errors=errors)


async def _locked(change: Change) -> None:
    """Before a change: fields a rule locks on the record as it is may not change."""
    if change.operation != "write" or not change.values:
        return
    rules = await _rules(change.records.conn, change.table.key, "read_only")
    locked = {
        name
        for rule in rules
        for name in cast(list[str], rule.fields)
        if name in change.values and await _holds(change, rule.condition)
    }
    if not locked:
        return
    before = await _stored(change, sorted(locked))
    changed = [
        n for n in sorted(locked) if before.get(n) != _given(change.table, n, change.values[n])
    ]
    if changed:
        raise _refuse(
            [FieldError(n, "read_only", "This field can't be changed now.") for n in changed]
        )


async def _required(change: Change) -> None:
    """After a change: fields a rule requires on the record as it now is may not be empty."""
    if change.operation not in ("create", "write"):
        return
    rules = await _rules(change.records.conn, change.table.key, "required")
    wanted = {
        name
        for rule in rules
        for name in cast(list[str], rule.fields)
        if await _holds(change, rule.condition)
    }
    if not wanted:
        return
    now = await _stored(change, sorted(wanted))
    empty = [n for n in sorted(wanted) if now.get(n) is None or now.get(n) == ""]
    if empty:
        raise _refuse([FieldError(n, "required", "This field is needed here.") for n in empty])


async def _fill_rules(conn: AsyncConnection, table: str) -> list[Any]:
    await switch_role(conn, "sys")
    return list(
        await conn.execute(
            text(
                "SELECT r.condition, r.fill FROM sys.form_rule r"
                " JOIN sys.module m ON m.name = r.module"
                ' WHERE r."table" = :t AND r.deleted IS NULL AND r.fill IS NOT NULL'
                " AND m.enabled AND m.deleted IS NULL ORDER BY r.name"
            ),
            {"t": table},
        )
    )


def _named_in(condition: Any) -> set[str]:
    """The fields a condition looks at."""
    if isinstance(condition, list):
        return {n for part in cast(list[Any], condition) for n in _named_in(part)}
    if not isinstance(condition, dict):
        return set()
    node = cast(dict[str, Any], condition)
    if "field" in node:
        return {str(node["field"])}
    return _named_in([v for k, v in node.items() if k in ("and", "or", "not")])


async def _holds_for(change: Change, values: dict[str, Any], condition: Any) -> bool:
    """Whether the condition holds for the record as it will be: what's stored (when changing),
    with the values given over it."""
    if condition is None:
        return True
    records, table = change.records, change.table
    await switch_role(records.conn, table.schema)
    query = Query()
    columns: list[str] = []
    for f in table.active_fields():
        column = ident(f.name)
        kind = f.type.column_type(f.field.options)
        if f.name in values:
            value = _given(table, f.name, values[f.name])
            if kind == "jsonb":
                value = None if value is None else json.dumps(value)
            columns.append(f"CAST({query.bind(value)} AS {kind}) AS {column}")
        elif change.sys_id is not None:
            columns.append(f"s.{column}")
        else:
            columns.append(f"CAST(NULL AS {kind}) AS {column}")
    source = ""
    if change.sys_id is not None:
        where = query.bind(change.sys_id)
        source = f" FROM {qualified(table.schema, table.name)} s WHERE s.sys_id = {where}"
    sql = Filter(records.catalog, records.situation, query).sql(table, "t", condition)
    found = await records.conn.scalar(
        text(f"SELECT {sql} FROM (SELECT {', '.join(columns)}{source}) t"),  # noqa: S608
        query.params,
    )
    return bool(found)


async def _fill(change: Change) -> None:
    """Before a new record or a change is saved, and before the table's own hooks: empty fields a
    rule fills while its condition holds. A change fills only when it touches the condition."""
    if change.operation not in ("create", "write"):
        return
    rules = await _fill_rules(change.records.conn, change.table.key)
    if not rules:
        return
    table = change.table
    stored = (
        await _stored(change, [f.name for f in table.active_fields()])
        if change.sys_id is not None
        else {}
    )
    for rule in rules:
        if change.sys_id is not None and not (_named_in(rule.condition) & change.values.keys()):
            continue
        current = stored | change.values
        empty = [
            name
            for name in cast(dict[str, Any], rule.fill)
            if current.get(name) is None or current.get(name) == ""
        ]
        if not empty or not await _holds_for(change, change.values, rule.condition):
            continue
        for name in empty:
            f = field_of(table, name)
            raw = rule.fill[name]
            if f is not None:
                change.values[name] = (
                    value_of(f, raw, change.records.situation) if is_dynamic(f, raw) else raw
                )


async def _check_rule(change: Change) -> None:
    """A rule an administrator makes as a record is held to what a module's rules are."""
    if change.operation not in ("create", "write") or not (
        {"fill", "condition", "table"} & change.values.keys()
    ):
        return
    values = change.values
    if change.sys_id is not None:
        values = (
            await _stored(change, ["table", "condition", "hide", "required", "read_only", "fill"])
            | values
        )
    table = change.records.catalog.table(str(values.get("table") or ""))
    if table is None:
        return  # the table field's own check names that
    found = [FieldError("fill", "bad_fill", m) for m in fill_problems(table, values.get("fill"))]
    only_fills = bool(values.get("fill")) and not (
        values.get("hide") or values.get("required") or values.get("read_only")
    )
    operators = FILL_OPERATORS if only_fills else RULE_OPERATORS
    found += [
        FieldError("condition", "not_for_rules", m)
        for m in _subset_problems(values.get("condition"), table, operators)
    ]
    if values.get("condition") is not None and not found:
        try:
            Filter(change.records.catalog, change.records.situation, Query()).sql(
                table, "t", values["condition"]
            )
        except Problem as problem:
            found += [FieldError("condition", e.code, e.message) for e in problem.errors]
    if found:
        raise _refuse(found)


TABLE_HOOKS = (
    TableHook(EVERY_TABLE, before=_locked, after=_required, fill=_fill),
    TableHook("sys.form_rule", before=_check_rule),
)
