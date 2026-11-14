"""Writing a module's form rules into records, and enforcing them on every save.

The browser applies the rules while the person types; the server applies them again, whatever
the browser did: a field a rule locks may not change, and one it requires may not stay empty.
"""

import re
import uuid
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import Filter, Query, Situation, field_of
from p42cloud.data.hooks import EVERY_TABLE, Change, TableHook
from p42cloud.data.sql import ident, qualified
from p42cloud.data.types import Invalid
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import sync
from p42cloud.ui.rules.models import RULE_OPERATORS, RULE_VARIABLES, FormRule

RULE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _subset_problems(node: Any, table: TableDef) -> list[str]:
    """What in a condition the browser couldn't evaluate the same way."""
    if node is None:
        return []
    if not isinstance(node, dict):
        return ["A condition or a group (and, or, not) was expected."]
    group = cast(dict[str, Any], node)
    if "and" in group or "or" in group:
        parts = group.get("and", group.get("or"))
        if not isinstance(parts, list):
            return ["A group lists its conditions."]
        return [p for part in cast(list[Any], parts) for p in _subset_problems(part, table)]
    if "not" in group:
        return _subset_problems(group["not"], table)
    name = group.get("field")
    found: list[str] = []
    if not isinstance(name, str) or "." in name or field_of(table, name) is None:
        found.append(f"Rules use the table's own fields, not {name!r}.")
    if group.get("op", "eq") not in RULE_OPERATORS:
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
    found += [
        FieldError(where, "not_for_rules", m) for m in _subset_problems(rule.condition, table)
    ]
    if rule.condition is not None and not found:
        try:
            Filter(catalog, Situation(uuid.UUID(int=0)), Query()).sql(table, "t", rule.condition)
        except Problem as problem:
            found += [FieldError(where, e.code, e.message) for e in problem.errors]
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
        }
        for r in rules
    ]
    await sync(
        conn,
        "sys.form_rule",
        module,
        rows,
        frozenset({"condition", "hide", "required", "read_only"}),
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


TABLE_HOOKS = (TableHook(EVERY_TABLE, before=_locked, after=_required),)
