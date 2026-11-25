"""Writing state flows into records, and holding every change of a state to them.

A record of a table with a flow starts in its initial state; afterwards its state changes only
along a transition from the state it's in, by someone holding one of the transition's roles (if
it names any), while the record matches the transition's condition (if it has one). Each
transition is also a button (a UI action setting the state), shown where it applies.
"""

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.conditions import Filter, Query
from p42cloud.data.declared import label_of, sync
from p42cloud.data.hooks import EVERY_TABLE, Change, TableHook
from p42cloud.data.presenting import translated
from p42cloud.data.sql import ident, qualified
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.services.states.models import Flow

NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _problems(catalog: Catalog, module: str, flow: Flow) -> list[FieldError]:
    where = f"flows.{flow.table}.{flow.field}"
    table = catalog.table(flow.table)
    if table is None or table.schema != module:
        return [FieldError(where, "not_its_table", f"{flow.table} isn't one of {module}'s tables.")]
    field = next((f for f in table.active_fields() if f.name == flow.field), None)
    if field is None or field.field.type != "choice":
        return [FieldError(where, "not_a_choice", f"{flow.field} isn't a choice field.")]
    states = {v["value"] for v in field.field.options.get("values", [])}
    found: list[FieldError] = []
    if flow.initial not in states:
        found.append(FieldError(where, "unknown_state", f"No state {flow.initial!r}."))
    for t in flow.transitions:
        if not NAME.match(t.name):
            found.append(FieldError(f"{where}.{t.name}", "bad_name", "a-z, 0-9 and _."))
        for state in (t.from_state, t.to_state):
            if state is not None and state not in states:
                found.append(FieldError(f"{where}.{t.name}", "unknown_state", f"No {state!r}."))
        if t.from_state == t.to_state:
            found.append(FieldError(f"{where}.{t.name}", "no_move", "It goes nowhere."))
    return found


async def write_flows(
    conn: AsyncConnection, catalog: Catalog, module: str, flows: tuple[Flow, ...]
) -> None:
    """Brings the module's flows and transitions to its declaration (as the owner)."""
    found = [p for f in flows for p in _problems(catalog, module, f)]
    names = [t.name for f in flows for t in f.transitions]
    if len(set(names)) != len(names):
        found.append(FieldError("flows", "duplicate", "A transition's name is used twice."))
    if len({(f.table, f.field) for f in flows}) != len(flows):
        found.append(FieldError("flows", "duplicate", "One flow per field."))
    if found:
        raise Problem(422, "invalid_flows", "The state flows can't be used.", errors=found)
    await sync(
        conn,
        "sys.state_flow",
        module,
        [
            {
                "name": f"{f.table}.{f.field}",
                "table": f.table,
                "field": f.field,
                "initial": f.initial,
            }
            for f in flows
        ],
        frozenset(),
    )
    await sync(
        conn,
        "sys.state_transition",
        module,
        [
            {
                "name": f"{module}.{t.name}",
                "flow": f"{f.table}.{f.field}",
                "label": label_of(t.label),
                "from_state": t.from_state,
                "to_state": t.to_state,
                "roles": list(t.roles) or None,
                "condition": t.condition,
            }
            for f in flows
            for t in f.transitions
        ],
        frozenset({"label", "roles", "condition"}),
    )


# ---- holding changes to the flows ----


async def _flows(conn: AsyncConnection, table: str) -> list[Any]:
    await switch_role(conn, "sys")
    return list(
        await conn.execute(
            text(
                "SELECT name, field, initial FROM sys.state_flow"
                ' WHERE "table" = :t AND deleted IS NULL'
            ),
            {"t": table},
        )
    )


def _label(change: Change, field: str, state: Any) -> str:
    """A state as people read it: its choice label in the person's language."""
    found = next((f for f in change.table.active_fields() if f.name == field), None)
    values: list[dict[str, Any]] = found.field.options.get("values", []) if found else []
    label = next((v.get("label") for v in values if v["value"] == state), None)
    return str(translated(label, change.records.situation.language) or state)


def _refuse(field: str, code: str, message: str) -> Problem:
    return Problem(
        422, "validation", "Some fields aren't valid.", errors=[FieldError(field, code, message)]
    )


async def _holds(change: Change, condition: Any) -> bool:
    if condition is None:
        return True
    records, table = change.records, change.table
    await switch_role(records.conn, table.schema)
    query = Query()
    sql = Filter(records.catalog, records.situation, query).sql(table, "t", condition)
    return bool(
        await records.conn.scalar(
            text(
                f"SELECT {sql} FROM {qualified(table.schema, table.name)} t"  # noqa: S608
                f" WHERE t.sys_id = {query.bind(change.sys_id)}"
            ),
            query.params,
        )
    )


async def _holds_role(change: Change, roles: list[str]) -> bool:
    if not roles or change.records.access.unrestricted:
        return True
    await switch_role(change.records.conn, "sys")
    return bool(
        await change.records.conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM sys.group_role gr"
                ' JOIN sys.role r ON r.sys_id = gr.role WHERE gr."group" = ANY(:groups)'
                " AND gr.deleted IS NULL AND r.deleted IS NULL"
                " AND r.name = ANY(:roles))"
            ),
            {"groups": list(change.records.access.groups), "roles": roles},
        )
    )


async def _needs_approval(conn: AsyncConnection, transition: str) -> bool:
    await switch_role(conn, "sys")
    return bool(
        await conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM sys.approval_rule WHERE name = :n"
                " AND deleted IS NULL)"
            ),
            {"n": transition},
        )
    )


async def _check_move(change: Change, flow: Any) -> None:
    field = str(flow.field)
    new = change.values[field]
    table = change.table
    await switch_role(change.records.conn, table.schema)
    current = await change.records.conn.scalar(
        text(
            f"SELECT {ident(field)} FROM {qualified(table.schema, table.name)}"  # noqa: S608
            " WHERE sys_id = :id"
        ),
        {"id": change.sys_id},
    )
    if new == current or change.records.access.unrestricted:  # the system: imports, migrations
        return
    await switch_role(change.records.conn, "sys")
    moves = list(
        await change.records.conn.execute(
            text(
                "SELECT name, roles, condition FROM sys.state_transition WHERE flow = :f"
                " AND deleted IS NULL AND to_state = :to"
                " AND (from_state IS NULL OR from_state = :from)"
            ),
            {"f": flow.name, "to": new, "from": current},
        )
    )
    said = f"From {_label(change, field, current)} it can't go to {_label(change, field, new)}."
    if not moves:
        raise _refuse(field, "not_allowed", said)
    for move in moves:
        if await _holds_role(change, list(move.roles or [])) and await _holds(
            change, move.condition
        ):
            if await _needs_approval(change.records.conn, str(move.name)):
                raise Problem(
                    409, "needs_approval", "This move needs approval: its button asks for it."
                )
            return
    raise _refuse(field, "not_allowed", f"{said[:-1]} now, or not by you.")


async def _hold_to_flows(change: Change) -> None:
    """Before a change: a new record starts where its flow begins; a state moves only along a
    transition."""
    if change.operation not in ("create", "write"):
        return
    flows = await _flows(change.records.conn, change.table.key)
    for flow in flows:
        field = str(flow.field)
        if change.operation == "create":
            given = change.values.get(field)
            if given is None:
                change.values[field] = flow.initial
            elif given != flow.initial and not change.records.access.unrestricted:
                raise _refuse(
                    field,
                    "not_allowed",
                    f"A new record starts in {_label(change, field, flow.initial)}.",
                )
        elif field in change.values:
            await _check_move(change, flow)


TABLE_HOOKS = (TableHook(EVERY_TABLE, before=_hold_to_flows),)
