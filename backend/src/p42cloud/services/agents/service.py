"""Agents work for the installation from other networks, through its public API only and only
outbound: enrolled once with a one-time code, each gets its own service account and API token;
it fetches its tasks, does them and sends the results back. Modules hand out tasks
(`Client.agent_task`) and get the results as the event `sys.agent_task_done`.
"""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.security.sign_in.events import event
from p42cloud.security.tokens.service import make_token
from p42cloud.services.events.outbox import publish

CODE_LIFE = timedelta(hours=24)
TOKEN_LIFE = timedelta(days=365)
QUIET_AFTER = timedelta(minutes=15)
MAX_TASKS = 50


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


async def new_code(conn: AsyncConnection, name: str) -> tuple[str, datetime]:
    """A one-time code for enrolling an agent of this name; shown once."""
    await switch_role(conn, "sys")
    code = "-".join(secrets.token_hex(2).upper() for _ in range(4))
    expires = datetime.now(UTC) + CODE_LIFE
    await conn.execute(
        text("INSERT INTO sys.agent_code (name, code, expires) VALUES (:n, :c, :e)"),
        {"n": name.strip()[:80], "c": _hash(code), "e": expires},
    )
    await event(conn, "agent_code_made", agent=name)
    return code, expires


async def enrol(
    conn: AsyncConnection, code: str, version: str, host: str, address: str | None
) -> dict[str, Any]:
    """The code, once: the agent, its service account and its token (shown once)."""
    await switch_role(conn, "sys")
    row = (
        await conn.execute(
            text(
                "SELECT sys_id, name FROM sys.agent_code WHERE code = :c AND used IS NULL"
                " AND expires > now() AND deleted IS NULL FOR UPDATE"
            ),
            {"c": _hash(code)},
        )
    ).first()
    if row is None:
        await event(conn, "agent_enrol_refused", address=address)
        raise Problem(403, "bad_code", "The code isn't valid (any more).")
    await conn.execute(
        text("UPDATE sys.agent_code SET used = now() WHERE sys_id = :id"), {"id": row.sys_id}
    )
    person = await conn.scalar(
        text(
            "INSERT INTO sys.person (name, kind, active, emergency_access)"
            " VALUES (:n, 'service', true, false)"
            " RETURNING sys_id"
        ),
        {"n": f"Agent {row.name}"},
    )
    assert isinstance(person, uuid.UUID)  # noqa: S101 - RETURNING gives it
    now = datetime.now(UTC)
    agent = await conn.scalar(
        text(
            "INSERT INTO sys.agent (name, person, version, host, address, enrolled, last_seen,"
            " active, quiet) VALUES (:n, :p, :v, :h, :a, :t, :t, true, false)"
            " ON CONFLICT (name) WHERE deleted IS NULL DO UPDATE SET person = :p, version = :v,"
            " host = :h, address = :a, enrolled = :t, last_seen = :t, active = true, quiet = false"
            " RETURNING sys_id"
        ),
        {"n": row.name, "p": person, "v": version[:40], "h": host[:253], "a": address, "t": now},
    )
    made = await make_token(conn, person, f"agent {row.name}", expires=now + TOKEN_LIFE)
    await event(conn, "agent_enrolled", agent=str(agent))
    await publish(
        conn,
        "sys.agent_enrolled",
        about=("sys.agent", agent),
        data={"name": row.name, "version": version, "host": host},
    )
    return {"agent": str(agent), "name": row.name, "token": made.token}


async def _agent_of(conn: AsyncConnection, person: uuid.UUID) -> Any:
    await switch_role(conn, "sys")
    row = (
        await conn.execute(
            text(
                "SELECT sys_id, name, quiet FROM sys.agent WHERE person = :p AND active"
                " AND deleted IS NULL"
            ),
            {"p": person},
        )
    ).first()
    if row is None:
        raise Problem(403, "not_an_agent", "Only an enrolled agent asks for its tasks.")
    return row


async def tasks_for(
    conn: AsyncConnection, person: uuid.UUID, version: str | None, address: str | None
) -> list[dict[str, Any]]:
    """An agent asks for its work: it's seen, and its waiting tasks are now running."""
    agent = await _agent_of(conn, person)
    await conn.execute(
        text(
            "UPDATE sys.agent SET last_seen = now(), quiet = false,"
            " version = coalesce(:v, version), address = coalesce(:a, address) WHERE sys_id = :id"
        ),
        {"v": version, "a": address, "id": agent.sys_id},
    )
    if agent.quiet:
        await publish(
            conn, "sys.agent_back", about=("sys.agent", agent.sys_id), data={"name": agent.name}
        )
    rows = (
        await conn.execute(
            text(
                "UPDATE sys.agent_task SET state = 'running', started = now() WHERE sys_id IN"
                " (SELECT sys_id FROM sys.agent_task WHERE agent = :a AND state = 'queued'"
                " AND deleted IS NULL ORDER BY created LIMIT :most FOR UPDATE SKIP LOCKED)"
                " RETURNING sys_id, kind, payload"
            ),
            {"a": agent.sys_id, "most": MAX_TASKS},
        )
    ).all()
    return [{"task": str(r.sys_id), "kind": r.kind, "payload": r.payload} for r in rows]


async def report(
    conn: AsyncConnection, person: uuid.UUID, task: uuid.UUID, ok: bool, result: Any
) -> None:
    """An agent's answer to one of its tasks; the module that asked hears of it."""
    agent = await _agent_of(conn, person)
    row = (
        await conn.execute(
            text(
                "UPDATE sys.agent_task SET state = :s, result = CAST(:r AS jsonb), finished = now()"
                " WHERE sys_id = :t AND agent = :a AND state = 'running' RETURNING module, kind"
            ),
            {
                "s": "done" if ok else "failed",
                "r": json.dumps(result),
                "t": task,
                "a": agent.sys_id,
            },
        )
    ).first()
    if row is None:
        raise Problem(404, "not_found", "No such running task of this agent.")
    await publish(
        conn,
        "sys.agent_task_done",
        about=("sys.agent_task", task),
        data={"module": row.module, "kind": row.kind, "ok": ok, "agent": str(agent.sys_id)},
    )


async def queue(
    conn: AsyncConnection, agent: uuid.UUID, module: str, kind: str, payload: Any
) -> uuid.UUID:
    """A task for an agent, from a module."""
    await switch_role(conn, "sys")
    found = await conn.scalar(
        text(
            "INSERT INTO sys.agent_task (agent, module, kind, payload, state)"
            " SELECT sys_id, :m, :k, CAST(:p AS jsonb), 'queued' FROM sys.agent"
            " WHERE sys_id = :a AND active AND deleted IS NULL RETURNING sys_id"
        ),
        {"a": agent, "m": module, "k": kind[:60], "p": json.dumps(payload)},
    )
    if not isinstance(found, uuid.UUID):
        raise Problem(404, "no_agent", "There's no such active agent.")
    return found


async def mark_quiet(conn: AsyncConnection) -> int:
    """Agents not seen for 15 minutes are quiet (once each): an event says so."""
    await switch_role(conn, "sys")
    rows = (
        await conn.execute(
            text(
                "UPDATE sys.agent SET quiet = true WHERE active AND NOT quiet AND deleted IS NULL"
                " AND last_seen < :since RETURNING sys_id, name"
            ),
            {"since": datetime.now(UTC) - QUIET_AFTER},
        )
    ).all()
    for r in rows:
        await publish(conn, "sys.agent_quiet", about=("sys.agent", r.sys_id), data={"name": r.name})
    return len(rows)
