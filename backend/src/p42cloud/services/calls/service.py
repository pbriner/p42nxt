"""Recording outside calls with a module's change, and the worker delivering them.

States: pending → sending → answered → done; a definite no from the other side → failed. An
answer lost on the way (a timeout, a worker stopping mid-call) makes the call `unknown`: before
anything is sent again, the module's `reconcile` asks the other side by the call's key whether
it happened. So a call is made at most once, as far as the other side can tell.
"""

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declared import label_of, sync
from p42cloud.data.records import Records
from p42cloud.kernel.context import EVERYTHING, Context, current, working_as
from p42cloud.kernel.db import switch_role, transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.service import access_for
from p42cloud.services.calls.models import CallRun, GaveUp, OutsideCall, Refused
from p42cloud.services.connections.service import connect
from p42cloud.services.jobs.service import account_of

CALL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_RETRIES = 10
FIRST_WAIT = timedelta(seconds=30)
SEND_LIMIT = 60.0  # seconds a send or a reconcile may take
STUCK = timedelta(minutes=5)  # a call `sending` this long: its worker stopped
MAX_MESSAGE = 2000

# The installed modules' calls, by name: (module, call). Filled when composing.
CALLS: dict[str, tuple[str, OutsideCall]] = {}


def register(module: str, calls: tuple[OutsideCall, ...]) -> None:
    for call in calls:
        CALLS[f"{module}.{call.name}"] = (module, call)


def _system() -> Context:
    return Context(channel="worker", access=EVERYTHING)


async def write_calls(
    conn: AsyncConnection, module: str, calls: tuple[OutsideCall, ...], types: set[str]
) -> None:
    found: list[FieldError] = []
    for call in calls:
        where = f"calls.{call.name}"
        if not CALL_NAME.match(call.name):
            found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
        if call.connection_type not in types:
            found.append(
                FieldError(
                    where,
                    "unknown_type",
                    f"{module} has no connection type {call.connection_type!r}.",
                )
            )
        if not 0 <= call.retries <= MAX_RETRIES:
            found.append(FieldError(where, "bad_retries", f"0 to {MAX_RETRIES} retries."))
    if len({c.name for c in calls}) != len(calls):
        found.append(FieldError("calls", "duplicate", "A call's name is used twice."))
    if found:
        raise Problem(422, "invalid_calls", "The outside calls can't be used.", errors=found)
    rows = [
        {
            "name": f"{module}.{c.name}",
            "label": label_of(c.label),
            "connection_type": f"{module}.{c.connection_type}",
            "retries": c.retries,
        }
        for c in calls
    ]
    await sync(conn, "sys.call_type", module, rows, frozenset({"label"}))


# ---- recording ----


async def record(
    records: Records,
    name: str,
    payload: dict[str, Any],
    *,
    owner: uuid.UUID | None = None,
    about: tuple[str, uuid.UUID | str] | None = None,
) -> uuid.UUID:
    """Records a call in the caller's transaction: it's made only if their change is kept."""
    found = CALLS.get(name)
    if found is None:
        raise Problem(404, "no_call", "There's no such outside call.", detail=name)
    module, _ = found
    who = owner or records.situation.person
    own = who == records.situation.person
    if not (own or records.access.unrestricted or current().channel.startswith(f"job:{module}.")):
        raise Problem(403, "forbidden", "Calls are made for oneself, or by the module's own jobs.")
    try:
        body = json.dumps(payload)
    except (TypeError, ValueError) as error:
        raise Problem(422, "validation", "What a call sends is JSON.") from error
    await switch_role(records.conn, "sys")
    made = await records.conn.scalar(
        text(
            "INSERT INTO sys.outside_call (call, module, owner, key, payload, record_table,"
            " record_id, state, attempts, not_before) VALUES (:call, :module, :owner, :key,"
            " CAST(:payload AS jsonb), :table, :id, 'pending', 0, now()) RETURNING sys_id"
        ),
        {
            "call": name,
            "module": module,
            "owner": who,
            "key": uuid.uuid7().hex,
            "payload": body,
            "table": about[0] if about else None,
            "id": str(about[1]) if about else None,
        },
    )
    if not isinstance(made, uuid.UUID):
        raise RuntimeError("no call recorded")
    return made


# ---- delivering ----


async def _claim(engine: AsyncEngine, now: datetime) -> Any | None:
    """The next call to work on, now `sending` (committed, so a stop mid-call leaves a trace)."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            return (
                await conn.execute(
                    text(
                        "UPDATE sys.outside_call c SET state = CASE WHEN c.state = 'answered'"
                        " THEN 'answered' ELSE 'sending' END, attempts = c.attempts + 1,"
                        " sent = :now"
                        " FROM (SELECT o.sys_id, o.state AS was, t.retries FROM sys.outside_call o"
                        " JOIN sys.call_type t ON t.name = o.call"
                        " WHERE o.deleted IS NULL"
                        " AND o.state IN ('pending', 'unknown', 'answered')"
                        " AND o.attempts <= t.retries"
                        " AND (o.not_before IS NULL OR o.not_before <= :now)"
                        " ORDER BY o.not_before NULLS FIRST LIMIT 1"
                        " FOR UPDATE OF o SKIP LOCKED) due"
                        " WHERE c.sys_id = due.sys_id"
                        " RETURNING c.sys_id, c.call, c.module, c.owner, c.key, c.payload,"
                        " c.record_table, c.record_id, c.attempts, c.answer, due.was, due.retries"
                    ),
                    {"now": now},
                )
            ).first()


async def _as_module(engine: AsyncEngine, module: str, call: str, ident: uuid.UUID) -> Context:
    person = account_of(module)
    with working_as(Context(channel="worker", person_id=person)):
        async with transaction(engine, "sys") as conn:
            access = await access_for(conn, person)
    return Context(
        channel=f"job:{module}.{call}", person_id=person, request_id=ident, access=access
    )


async def _set(engine: AsyncEngine, ident: uuid.UUID, **values: Any) -> None:
    sets = ", ".join(
        f"{k} = CAST(:{k} AS jsonb)" if k == "answer" else f"{k} = :{k}" for k in values
    )
    params = {k: json.dumps(v) if k == "answer" else v for k, v in values.items()}
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                # Only this module's own column names go into the text.
                text(f"UPDATE sys.outside_call SET {sets} WHERE sys_id = :id"),  # noqa: S608
                params | {"id": ident},
            )


def _later(attempt: int) -> datetime:
    return datetime.now(UTC) + FIRST_WAIT * (2 ** max(0, attempt - 1))


async def _exchange(
    engine: AsyncEngine, row: Any, call: OutsideCall, run: CallRun
) -> dict[str, Any]:
    """Asks first if the answer was lost; sends only if the call didn't happen."""
    context = await _as_module(engine, row.module, call.name, row.sys_id)
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            async with connect(
                records, f"{row.module}.{call.connection_type}", owner=row.owner
            ) as http:
                if row.was == "unknown":
                    known = await asyncio.wait_for(call.reconcile(http, run), SEND_LIMIT)
                    if known is not None:
                        return known
                return await asyncio.wait_for(call.send(http, run), SEND_LIMIT)


async def _react(
    engine: AsyncEngine, row: Any, call: OutsideCall, run: CallRun, answer: dict[str, Any]
) -> None:
    """The module's `done`, and `done` as the state, in one transaction."""
    context = await _as_module(engine, row.module, call.name, row.sys_id)
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            if call.done is not None:
                await call.done(records, run, answer)
            await switch_role(conn, "sys")
            await conn.execute(
                text(
                    "UPDATE sys.outside_call SET state = 'done', message = NULL,"
                    " answer = CAST(:answer AS jsonb) WHERE sys_id = :id"
                ),
                {"id": row.sys_id, "answer": json.dumps(answer)},
            )


async def _attempt(
    engine: AsyncEngine, row: Any, call: OutsideCall, run: CallRun
) -> tuple[str, dict[str, Any]]:
    """What came of one exchange: the state, and what to record with it."""
    try:
        answer = await _exchange(engine, row, call, run)
    except Refused as error:
        return "failed", {"message": str(error)[:MAX_MESSAGE] or "Refused."}
    except httpx.ConnectError as error:  # nothing left: safe to send again
        return "pending", {"not_before": _later(row.attempts), "message": str(error)[:MAX_MESSAGE]}
    except Exception as error:  # sent or not, nobody knows: ask first next time
        said = f"{type(error).__name__}: {error}"[:MAX_MESSAGE]
        return "unknown", {"not_before": _later(row.attempts), "message": said}
    return "answered", {"answer": answer, "message": None}


async def deliver(engine: AsyncEngine, now: datetime | None = None) -> str | None:
    """Works on one call; returns its new state, or None if there was nothing to do."""
    row = await _claim(engine, now or datetime.now(UTC))
    if row is None:
        return None
    found = CALLS.get(row.call)
    if found is None:
        state, values = "failed", {"message": "The module that makes this call isn't installed."}
    else:
        _, call = found
        run = CallRun(
            row.key,
            cast(dict[str, Any], row.payload or {}),
            row.owner,
            row.record_table,
            row.record_id,
            row.attempts,
            datetime.now(UTC),
        )
        if row.was == "answered":
            state, values = "answered", {"answer": row.answer}
        else:
            state, values = await _attempt(engine, row, call, run)
        if state == "answered":
            try:
                await _react(engine, row, call, run, cast(dict[str, Any], values["answer"] or {}))
                return "done"
            except Exception as error:  # the answer is kept; the reaction is tried again
                said = f"{type(error).__name__}: {error}"[:MAX_MESSAGE]
                values |= {"not_before": _later(row.attempts), "message": said}
        ending = _ending(row, state, str(values.get("message") or ""))
        if ending is not None:
            # Not sent at all: failed. Maybe sent: stays unknown, tried no more.
            state = "failed" if ending.why != "unclear" else "unknown"
            await _set(engine, row.sys_id, state=state, **values)
            if call.gave_up is not None:
                await _give_up(engine, row, call, run, ending)
            return state
    await _set(engine, row.sys_id, state=state, **values)
    return state


def _ending(row: Any, state: str, message: str) -> GaveUp | None:
    """Whether this attempt ends the call without an answer."""
    if state == "failed":
        return GaveUp("refused", message)
    if state in ("pending", "unknown") and row.attempts > row.retries:
        return GaveUp("not_sent" if state == "pending" else "unclear", message)
    return None


async def _give_up(
    engine: AsyncEngine, row: Any, call: OutsideCall, run: CallRun, ending: GaveUp
) -> None:
    """The module's `gave_up`, as its service account, in its own transaction."""
    context = await _as_module(engine, row.module, call.name, row.sys_id)
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            if call.gave_up is not None:
                await call.gave_up(records, run, ending)


async def recover(engine: AsyncEngine, now: datetime | None = None) -> int:
    """Calls a stopped worker left `sending`: their answer is lost, so they're asked about."""
    now = now or datetime.now(UTC)
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            changed = await conn.execute(
                text(
                    "UPDATE sys.outside_call SET state = 'unknown', not_before = :now,"
                    " message = 'The worker stopped while sending; asking before sending again.'"
                    " WHERE state = 'sending' AND deleted IS NULL"
                    " AND sent < :cutoff"
                ),
                {"now": now, "cutoff": now - STUCK},
            )
            return changed.rowcount
