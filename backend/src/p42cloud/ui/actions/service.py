"""Writing a module's UI actions into records; which apply to which records; running them.

Running checks everything again, whatever the screen showed: the action exists and its module
is on, the person may read the record and do the action's operation on it, the condition holds,
the inputs are valid, and (if asked for) the second factor is recent. The work and its audit
happen in the request's transaction: all or nothing.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import Filter, Query, Situation, field_of
from p42cloud.data.presenting import translated
from p42cloud.data.records import Records
from p42cloud.data.types import FIELD_TYPES, Invalid
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.guards import RECENT
from p42cloud.security.sign_in.events import event
from p42cloud.ui.actions.models import (
    INPUT_TYPES,
    PLACES,
    ActionInput,
    ActionResult,
    ActionRun,
    Handler,
    UIAction,
)
from p42cloud.ui.declared import label_of, sync

ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
INPUT = re.compile(r"^@input\.([a-z][a-z0-9_]{0,39})$")
MAX_BULK = 500

# The handlers of the installed modules' actions, by name (filled when modules are composed).
HANDLERS: dict[str, Handler] = {}


def register(module: str, actions: tuple[UIAction, ...]) -> None:
    for action in actions:
        if action.handler is not None:
            HANDLERS[f"{module}.{action.name}"] = action.handler


def _input_problems(where: str, given: ActionInput) -> list[FieldError]:
    found: list[FieldError] = []
    if not ACTION_NAME.match(given.name):
        found.append(FieldError(where, "bad_input", f"Not an input name: {given.name!r}."))
    if given.type not in INPUT_TYPES:
        found.append(FieldError(where, "bad_input", f"Inputs are one of {sorted(INPUT_TYPES)}."))
    else:
        found += [
            FieldError(where, "bad_input", m)
            for m in FIELD_TYPES[given.type].option_problems(given.options)
        ]
    return found


def _set_problems(where: str, table: TableDef, action: UIAction) -> list[FieldError]:
    inputs = {i.name for i in action.inputs}
    found: list[FieldError] = []
    for name, value in (action.set or {}).items():
        f = field_of(table, name)
        if f is None or name in ("sys_id", "created", "created_by", "updated", "updated_by"):
            found.append(FieldError(where, "unknown_field", f"No field {name!r} to set."))
            continue
        given = INPUT.match(value) if isinstance(value, str) else None
        if given is not None:
            if given[1] not in inputs:
                found.append(FieldError(where, "unknown_input", f"No input {given[1]!r}."))
            continue
        try:
            f.type.check(value, f.field.options)
        except Invalid as error:
            found.append(FieldError(where, "bad_value", f"{name}: {error}"))
    return found


def _problems(catalog: Catalog, module: str, action: UIAction) -> list[FieldError]:
    where = f"actions.{action.name}"
    table = catalog.table(action.table)
    if table is None or table.retired or table.schema != module:
        return [FieldError(where, "unknown_table", "An action is on one of the module's tables.")]
    found: list[FieldError] = []
    if not ACTION_NAME.match(action.name):
        found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
    if not action.places or not set(action.places) <= PLACES:
        found.append(FieldError(where, "bad_places", f"Places are some of {sorted(PLACES)}."))
    if (action.handler is None) == (action.set is None):
        found.append(FieldError(where, "no_work", "An action either sets fields or runs code."))
    for given in action.inputs:
        found += _input_problems(where, given)
    found += _set_problems(where, table, action)
    if action.condition is not None:
        try:
            Filter(catalog, Situation(uuid.UUID(int=0)), Query()).sql(table, "t", action.condition)
        except Problem as problem:
            found += [FieldError(where, e.code, e.message) for e in problem.errors]
    return found


async def write_actions(
    conn: AsyncConnection, catalog: Catalog, module: str, actions: tuple[UIAction, ...]
) -> None:
    found = [p for a in actions for p in _problems(catalog, module, a)]
    if len({a.name for a in actions}) != len(actions):
        found.append(FieldError("actions", "duplicate", "An action's name is used twice."))
    if found:
        raise Problem(422, "invalid_actions", "The actions can't be used.", errors=found)
    rows = [
        {
            "name": f"{module}.{a.name}",
            "table": a.table,
            "label": label_of(a.label),
            "icon": a.icon,
            "places": list(a.places),
            "operation": a.operation,
            "condition": a.condition,
            "confirm": label_of(a.confirm),
            "inputs": [
                {
                    "name": i.name,
                    "label": label_of(i.label),
                    "type": i.type,
                    "required": i.required,
                    "options": i.options,
                }
                for i in a.inputs
            ]
            or None,
            "second_factor": a.second_factor,
            "set": a.set,
            "handler": a.handler is not None,
            "message": label_of(a.message),
            "go": a.go,
        }
        for a in actions
    ]
    await sync(
        conn,
        "sys.ui_action",
        module,
        rows,
        frozenset({"label", "places", "condition", "confirm", "inputs", "set", "message"}),
    )


# ---- which apply ----


async def _actions_of(conn: AsyncConnection, table: str, name: str | None = None) -> list[Any]:
    """The table's actions (or one), of modules switched on."""
    await switch_role(conn, "sys")
    return list(
        await conn.execute(
            text(
                "SELECT a.name, a.operation, a.condition, a.inputs, a.second_factor, a.set,"
                " a.handler, a.message, a.label, a.go FROM sys.ui_action a"
                " JOIN sys.module m ON m.name = a.module"
                ' WHERE a."table" = :t AND a.deleted IS NULL AND m.enabled AND m.deleted IS NULL'
                " AND (CAST(:n AS text) IS NULL OR a.name = :n) ORDER BY a.name"
            ),
            {"t": table, "n": name},
        )
    )


async def _holding(
    records: Records, table: TableDef, ids: list[uuid.UUID], condition: Any
) -> set[uuid.UUID]:
    """The records among `ids` for which the condition holds (every field counts)."""
    if condition is None or not ids:
        return set(ids)
    await switch_role(records.conn, table.schema)
    query = Query()
    sql = Filter(records.catalog, records.situation, query).sql(table, "t", condition)
    found = await records.conn.scalars(
        text(
            f"SELECT t.sys_id FROM {table.schema}.{table.name} t"  # noqa: S608
            f" WHERE t.sys_id = ANY({query.bind(ids)}) AND {sql}"
        ),
        query.params,
    )
    return set(found)


async def available(records: Records, table: TableDef, bodies: list[dict[str, Any]]) -> None:
    """Adds `_actions` to each record: the actions the person may run on it now."""
    actions = await _actions_of(records.conn, table.key)
    ids = [uuid.UUID(str(b["sys_id"])) for b in bodies if "sys_id" in b]
    allowed: dict[str, set[uuid.UUID]] = {}
    if actions and ids:
        operations = tuple({a.operation for a in actions})
        may = await records.may(table, ids, operations)
        for action in actions:
            permitted = [i for i in ids if may.get(i, {}).get(action.operation)]
            allowed[action.name] = await _holding(records, table, permitted, action.condition)
    for body in bodies:
        sys_id = uuid.UUID(str(body["sys_id"])) if "sys_id" in body else None
        body["_actions"] = [a.name for a in actions if sys_id in allowed.get(a.name, set())]


# ---- running ----


def _inputs(action: Any, given: dict[str, Any]) -> dict[str, Any]:
    declared: list[dict[str, Any]] = cast(list[dict[str, Any]], action.inputs or [])
    unknown = set(given) - {i["name"] for i in declared}
    errors = [FieldError(n, "unknown", "Not asked for.") for n in sorted(unknown)]
    checked: dict[str, Any] = {}
    for item in declared:
        value = given.get(item["name"])
        if value is None or value == "":
            if item.get("required", True):
                errors.append(FieldError(item["name"], "required", "This is needed."))
            continue
        try:
            checked[item["name"]] = FIELD_TYPES[item["type"]].check(
                value, item.get("options") or {}
            )
        except Invalid as error:
            errors.append(FieldError(item["name"], "invalid", str(error)))
    if errors:
        raise Problem(422, "validation", "Some inputs aren't valid.", errors=errors)
    return checked


def _values(action: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    """What the action sets, its `@input.<name>` replaced by what was entered."""
    values: dict[str, Any] = {}
    for name, value in cast(dict[str, Any], action.set or {}).items():
        given = INPUT.match(value) if isinstance(value, str) else None
        values[name] = inputs.get(given[1]) if given else value
    return values


@dataclass(frozen=True)
class _Chosen:
    """An action found and checked, with its inputs: ready to run on records."""

    table: TableDef
    action: Any
    inputs: dict[str, Any]


async def _one(
    records: Records, chosen: _Chosen, sys_id: uuid.UUID, etag: str | None
) -> dict[str, Any]:
    table, action, inputs = chosen.table, chosen.action, chosen.inputs
    record = await records.read(table.key, sys_id)  # 404 if they may not read it
    if etag is not None and etag != record.etag:
        raise Problem(412, "changed", "Someone changed the record in the meantime.")
    may = await records.may(table, [sys_id], (action.operation,))
    holds = await _holding(records, table, [sys_id], action.condition)
    if not may.get(sys_id, {}).get(action.operation) or sys_id not in holds:
        raise Problem(403, "not_available", "This action isn't available for this record now.")
    language = records.situation.language
    result: ActionResult | None = None
    if action.set is not None:
        await records.update(table.key, sys_id, _values(action, inputs), record.etag)
    else:
        handler = HANDLERS.get(action.name)
        if handler is None:
            raise Problem(409, "no_handler", "The module that runs this action isn't installed.")
        run_ = ActionRun(records, table.key, sys_id, records.to_json(table, record), inputs)
        result = await handler(run_)
    await event(records.conn, "ui_action", action=action.name, table=table.key, record=str(sys_id))
    await switch_role(records.conn, table.schema)
    return {
        "sys_id": str(sys_id),
        "message": (result.message if result else None) or translated(action.message, language),
        "go": (result.go if result else None) or action.go,
    }


async def _choose(records: Records, table_key: str, name: str, given: dict[str, Any]) -> _Chosen:
    table = records.catalog.table(table_key)
    found = await _actions_of(records.conn, table_key, name) if table else []
    if table is None or not found:
        raise Problem(404, "no_action", "There's no such action here.", detail=name)
    action = found[0]
    if action.second_factor:
        verified = current().verified_at
        if verified is None or datetime.now(UTC) - verified > RECENT:
            raise Problem(403, "verify_again", "Enter a code from your authenticator app again.")
    return _Chosen(table, action, _inputs(action, given))


async def run(  # noqa: PLR0913 - the request's parts; the options by keyword
    records: Records,
    table_key: str,
    name: str,
    sys_id: uuid.UUID,
    *,
    given: dict[str, Any],
    etag: str | None = None,
) -> dict[str, Any]:
    return await _one(records, await _choose(records, table_key, name, given), sys_id, etag)


async def run_many(
    records: Records, table_key: str, name: str, ids: list[uuid.UUID], *, given: dict[str, Any]
) -> list[dict[str, Any]]:
    """The action on each record, each on its own: one refused doesn't undo the others."""
    if len(ids) > MAX_BULK:
        raise Problem(422, "too_many", f"At most {MAX_BULK} records at once.")
    chosen = await _choose(records, table_key, name, given)
    results: list[dict[str, Any]] = []
    for sys_id in ids:
        try:
            async with records.conn.begin_nested():
                results.append({**await _one(records, chosen, sys_id, None), "ok": True})
        except Problem as problem:
            results.append(
                {
                    "sys_id": str(sys_id),
                    "ok": False,
                    "code": problem.code,
                    "title": problem.title,
                    "detail": problem.detail,
                }
            )
    return results
