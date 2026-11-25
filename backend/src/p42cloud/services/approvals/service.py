"""Approvals: a move of a state flow that needs other people's yes before it's made.

Pressing the move's button asks for approval instead of moving. The steps that apply (a step may
apply only above an amount) are taken in order; each step's approvers are the holders of its
role, never the person who asked, and the deputies of those away. One yes per step moves it on;
the last one makes the move, as the system. One no ends it, with a reason, and the record stays.
While an approval waits, the record can't be changed, and the move can't be made around it.
"""

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import TableDef
from p42cloud.data.hooks import EVERY_TABLE, Change, TableHook
from p42cloud.data.presenting import named, translated
from p42cloud.data.records import Records
from p42cloud.kernel.context import EVERYTHING, current, working_as
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.services.approvals.models import Approval
from p42cloud.services.notifications.service import send

ASKED, DECIDED = "sys.approval_asked", "sys.approval_decided"


# ---- the rules, from the flows ----


def rule_rows(module: str, moves: list[tuple[str, Approval]]) -> list[dict[str, Any]]:
    """The approval rules a module's transitions declare, as records."""
    return [
        {
            "name": f"{module}.{name}",
            "steps": [
                {k: v for k, v in (("role", s.role), ("field", s.field), ("above", s.above)) if v}
                for s in approval.steps
            ],
            "remind_after": approval.remind_after,
            "escalate_after": approval.escalate_after,
            "escalate_to": approval.escalate_to,
        }
        for name, approval in moves
    ]


async def write_rules(conn: AsyncConnection, module: str, rows: list[dict[str, Any]]) -> None:
    """Brings the module's approval rules to its declaration (as the owner). A rule an
    administrator changed is kept as they left it; rules no longer declared are retired."""
    for row in rows:
        values = {
            "name": row["name"],
            "module": module,
            "steps": json.dumps(row["steps"]),
            "remind_after": row["remind_after"],
            "escalate_after": row["escalate_after"],
            "escalate_to": row["escalate_to"],
        }
        changed = await conn.execute(
            text(
                "UPDATE sys.approval_rule SET steps = CAST(:steps AS jsonb),"
                " remind_after = :remind_after, escalate_after = :escalate_after,"
                " escalate_to = :escalate_to, module = :module, deleted = NULL"
                " WHERE name = :name AND NOT coalesce(changed, false)"
            ),
            values,
        )
        if changed.rowcount == 0:
            await conn.execute(
                text(
                    "INSERT INTO sys.approval_rule (name, module, steps, remind_after,"
                    " escalate_after, escalate_to, changed, custom) VALUES (:name, :module,"
                    " CAST(:steps AS jsonb), :remind_after, :escalate_after, :escalate_to,"
                    " false, false) ON CONFLICT (name) WHERE deleted IS NULL DO NOTHING"
                ),
                values,
            )
    await conn.execute(
        text(
            "UPDATE sys.approval_rule SET deleted = now() WHERE module = :m AND deleted IS NULL"
            " AND NOT custom AND NOT (name = ANY(:names))"
        ),
        {"m": module, "names": [r["name"] for r in rows]},
    )


async def rule_for(conn: AsyncConnection, transition: str) -> Any | None:
    """The rule of a transition (`<module>.<name>`), if its move needs approval."""
    await switch_role(conn, "sys")
    return (
        await conn.execute(
            text(
                "SELECT sys_id, name, steps, remind_after, escalate_after, escalate_to"
                " FROM sys.approval_rule WHERE name = :n AND deleted IS NULL"
            ),
            {"n": transition},
        )
    ).first()


# ---- who approves ----


async def _holders(conn: AsyncConnection, role: str) -> list[uuid.UUID]:
    """Everyone active in a group that holds the role."""
    await switch_role(conn, "sys")
    return list(
        await conn.scalars(
            text(
                "SELECT DISTINCT m.person FROM sys.membership m"
                ' JOIN sys.group_role gr ON gr."group" = m."group" AND gr.deleted IS NULL'
                " JOIN sys.role r ON r.sys_id = gr.role AND r.deleted IS NULL"
                " JOIN sys.person p ON p.sys_id = m.person AND p.deleted IS NULL AND p.active"
                " WHERE m.deleted IS NULL AND r.name = :role"
            ),
            {"role": role},
        )
    )


async def _administrators(conn: AsyncConnection) -> list[uuid.UUID]:
    await switch_role(conn, "sys")
    return list(
        await conn.scalars(
            text(
                "SELECT m.person FROM sys.membership m JOIN sys.person p ON p.sys_id = m.person"
                ' WHERE m."group" = :g AND m.deleted IS NULL AND p.deleted IS NULL AND p.active'
            ),
            {"g": ADMINISTRATORS},
        )
    )


async def _deputies(
    conn: AsyncConnection, people: list[uuid.UUID], day: date
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(deputy, for whom) for those of these people away today."""
    await switch_role(conn, "sys")
    rows = await conn.execute(
        text(
            "SELECT deputy, person FROM sys.deputy WHERE deleted IS NULL AND person = ANY(:p)"
            " AND starts <= :d AND ends >= :d"
        ),
        {"p": people, "d": day},
    )
    return [(r.deputy, r.person) for r in rows]


async def _add_approvers(  # noqa: PLR0913 - who, for which step, and why
    conn: AsyncConnection,
    approval: uuid.UUID,
    step: int,
    people: list[uuid.UUID],
    requester: uuid.UUID,
    *,
    escalation: bool = False,
) -> list[uuid.UUID]:
    """The step's approvers and the deputies of those away, never the person who asked."""
    rows: dict[tuple[uuid.UUID, uuid.UUID | None], None] = {}
    for person in people:
        if person != requester:
            rows[(person, None)] = None
    for deputy, away in await _deputies(conn, people, datetime.now(UTC).date()):
        if deputy != requester:
            rows[(deputy, away)] = None
    for person, away in rows:
        await conn.execute(
            text(
                "INSERT INTO sys.approver (approval, person, step, on_behalf_of, escalation, open)"
                " VALUES (:a, :p, :s, :b, :e, true)"
            ),
            {"a": approval, "p": person, "s": step, "b": away, "e": escalation},
        )
    return list(dict.fromkeys(person for person, _ in rows))


async def _start_step(
    conn: AsyncConnection, approval: uuid.UUID, step: int, steps: list[Any], requester: uuid.UUID
) -> list[uuid.UUID]:
    people = await _holders(conn, str(steps[step]["role"]))
    found = await _add_approvers(conn, approval, step, people, requester)
    if not found:  # nobody holds the role (but who asked): the administrators decide
        found = await _add_approvers(
            conn, approval, step, await _administrators(conn), requester, escalation=True
        )
    await conn.execute(
        text("UPDATE sys.approval SET step = :s, step_started = now() WHERE sys_id = :a"),
        {"s": step, "a": approval},
    )
    return found


# ---- asking ----


def _applies(step: dict[str, Any], values: dict[str, Any]) -> bool:
    field, above = step.get("field"), step.get("above")
    if not field or above is None:
        return True
    try:
        return Decimal(str(values.get(field) or 0)) > Decimal(str(above))
    except InvalidOperation:
        return True


async def pending(conn: AsyncConnection, table: str, record: uuid.UUID) -> Any | None:
    await switch_role(conn, "sys")
    return (
        await conn.execute(
            text(
                'SELECT sys_id, title FROM sys.approval WHERE "table" = :t AND record = :r'
                " AND state = 'pending' AND deleted IS NULL"
            ),
            {"t": table, "r": record},
        )
    ).first()


async def ask(  # noqa: PLR0913 - the record, then the move, by keyword
    records: Records,
    table: TableDef,
    record: uuid.UUID,
    *,
    rule: Any,
    move: dict[str, Any],
    label: str,
) -> str:
    """Asks for approval of a move; returns what to tell the person. A move whose steps don't
    apply to this record (all below their amounts) is made at once."""
    conn = records.conn
    if await pending(conn, table.key, record):
        raise Problem(409, "pending", "An approval for it is already waiting.")
    current_values = (await records.read(table.key, record)).values
    steps = [s for s in rule.steps if _applies(s, current_values)]
    if not steps:
        await _move(records, table, record, move)
        return f"{label}: done (no approval needed for this one)."
    names = await named(records, records.catalog, table.key, {record})
    shown = names.get(record, (None, False))[0] or str(record)
    title = f"{translated(table.label, 'en')} {shown}: {label}"[:255]
    person = current().person_id
    await switch_role(conn, "sys")
    approval = await conn.scalar(
        text(
            'INSERT INTO sys.approval (title, "table", record, rule, transition, requested_by,'
            " state, steps, step, step_started, escalated) VALUES (:title, :t, :r, :rule, :tr,"
            " :by, 'pending', CAST(:steps AS jsonb), 0, now(), false) RETURNING sys_id"
        ),
        {
            "title": title,
            "t": table.key,
            "r": record,
            "rule": rule.sys_id,
            "tr": rule.name,
            "by": person,
            "steps": json.dumps(steps),
        },
    )
    assert isinstance(approval, uuid.UUID)  # noqa: S101 - RETURNING gives it
    approvers = await _start_step(conn, approval, 0, steps, person)
    if not approvers:
        raise Problem(
            409,
            "no_approvers",
            "Nobody but you may approve it: give the role to someone else first.",
        )
    await _tell(conn, approvers, f"Your approval, please: {title}", table.key, record)
    return "Asked for approval."


async def _tell(
    conn: AsyncConnection, people: list[uuid.UUID], title: str, table: str, record: uuid.UUID
) -> None:
    if people:
        await send(
            conn, ASKED, title, people=people, link=f"/form/{table}/{record}", severity="info"
        )


async def _move(records: Records, table: TableDef, record: uuid.UUID, move: dict[str, Any]) -> None:
    """The move itself, as the system: whoever approved may not change the record otherwise."""
    with working_as(replace(current(), access=EVERYTHING)):
        system = Records(records.conn, records.catalog)
        found = await system.read(table.key, record)
        await system.update(table.key, record, move, found.etag)


# ---- deciding ----


async def _approval(conn: AsyncConnection, approval: uuid.UUID) -> Any:
    await switch_role(conn, "sys")
    row = (
        await conn.execute(
            text(
                'SELECT a.sys_id, a.title, a."table", a.record, a.transition, a.requested_by,'
                " a.state, a.steps, a.step, t.flow, t.to_state FROM sys.approval a"
                " JOIN sys.state_transition t ON t.name = a.transition AND t.deleted IS NULL"
                " WHERE a.sys_id = :a AND a.deleted IS NULL"
            ),
            {"a": approval},
        )
    ).first()
    if row is None:
        raise Problem(404, "not_found", "There's no such approval.")
    return row


async def decide(
    records: Records, approval: uuid.UUID, decision: str, note: str | None
) -> dict[str, Any]:
    """A yes or a no from one of the current step's approvers (or their deputy)."""
    conn = records.conn
    row = await _approval(conn, approval)
    person = current().person_id
    if row.state != "pending":
        raise Problem(409, "decided", "It's decided already.")
    mine = (
        await conn.execute(
            text(
                "SELECT on_behalf_of FROM sys.approver WHERE approval = :a AND step = :s"
                " AND person = :p AND open AND deleted IS NULL LIMIT 1"
            ),
            {"a": approval, "s": row.step, "p": person},
        )
    ).first()
    if mine is None:
        raise Problem(403, "not_yours", "It isn't waiting for your approval.")
    if decision == "reject" and not (note or "").strip():
        raise Problem(
            422,
            "validation",
            "Say why.",
            errors=[FieldError("note", "required", "Why do you reject it?")],
        )
    await conn.execute(
        text(
            "INSERT INTO sys.approval_decision (approval, step, person, on_behalf_of, decision,"
            " note) VALUES (:a, :s, :p, :b, :d, :n)"
        ),
        {
            "a": approval,
            "s": row.step,
            "p": person,
            "b": mine.on_behalf_of,
            "d": decision,
            "n": (note or "").strip() or None,
        },
    )
    await conn.execute(
        text("UPDATE sys.approver SET open = false WHERE approval = :a AND step = :s AND open"),
        {"a": approval, "s": row.step},
    )
    table = records.catalog.tables[str(row.table)]
    if decision == "reject":
        await _finish(conn, row, "rejected", (note or "").strip())
        await _told(conn, row, f"Rejected: {row.title}", note)
        return {"state": "rejected"}
    steps: list[Any] = list(row.steps)
    if row.step + 1 < len(steps):
        approvers = await _start_step(conn, approval, row.step + 1, steps, row.requested_by)
        await _tell(conn, approvers, f"Your approval, please: {row.title}", table.key, row.record)
        return {"state": "pending", "step": row.step + 1}
    field = str(row.flow).rsplit(".", 1)[1]
    await _finish(conn, row, "approved", None)
    await _move(records, table, row.record, {field: row.to_state})
    await _told(conn, row, f"Approved: {row.title}", None)
    return {"state": "approved"}


async def _finish(conn: AsyncConnection, row: Any, state: str, note: str | None) -> None:
    await switch_role(conn, "sys")
    await conn.execute(
        text("UPDATE sys.approval SET state = :s, decided = now(), note = :n WHERE sys_id = :a;"),
        {"s": state, "n": note, "a": row.sys_id},
    )
    await conn.execute(
        text("UPDATE sys.approver SET open = false WHERE approval = :a AND open"),
        {"a": row.sys_id},
    )


async def _told(conn: AsyncConnection, row: Any, title: str, note: str | None) -> None:
    await send(
        conn,
        DECIDED,
        title,
        people=(row.requested_by,),
        message=note,
        link=f"/form/{row.table}/{row.record}",
    )


async def withdraw(records: Records, approval: uuid.UUID) -> dict[str, Any]:
    """Who asked takes it back, while it waits."""
    row = await _approval(records.conn, approval)
    if row.requested_by != current().person_id:
        raise Problem(403, "not_yours", "Only who asked withdraws it.")
    if row.state != "pending":
        raise Problem(409, "decided", "It's decided already.")
    await _finish(records.conn, row, "withdrawn", None)
    return {"state": "withdrawn"}


# ---- reading ----


async def _names(records: Records, people: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    found = await named(records, records.catalog, "sys.person", people) if people else {}
    return {k: str(v[0]) for k, v in found.items()}


async def of_record(records: Records, table: TableDef, record: uuid.UUID) -> list[dict[str, Any]]:
    """A record's approvals, newest first: steps, approvers, decisions, and what the caller may
    do. Only for those who may read the record."""
    await records.read(table.key, record)  # 404 if they may not read it
    conn = records.conn
    await switch_role(conn, "sys")
    approvals = list(
        await conn.execute(
            text(
                "SELECT sys_id, title, state, steps, step, requested_by, created, decided, note"
                ' FROM sys.approval WHERE "table" = :t AND record = :r AND deleted IS NULL'
                " ORDER BY created DESC LIMIT 20"
            ),
            {"t": table.key, "r": record},
        )
    )
    ids = [a.sys_id for a in approvals]
    approvers = list(
        await conn.execute(
            text(
                "SELECT approval, person, step, on_behalf_of, open, escalation FROM sys.approver"
                " WHERE approval = ANY(:ids) AND deleted IS NULL ORDER BY created"
            ),
            {"ids": ids},
        )
    )
    decisions = list(
        await conn.execute(
            text(
                "SELECT approval, person, step, on_behalf_of, decision, note, created"
                " FROM sys.approval_decision WHERE approval = ANY(:ids) AND deleted IS NULL"
                " ORDER BY created"
            ),
            {"ids": ids},
        )
    )
    people = {a.requested_by for a in approvals} | {
        p for r in (*approvers, *decisions) for p in (r.person, r.on_behalf_of) if p is not None
    }
    names = await _names(records, people)
    me = current().person_id
    roles = await _role_labels(conn, records.situation.language)
    found: list[dict[str, Any]] = []
    for a in approvals:
        waiting = [r for r in approvers if r.approval == a.sys_id and r.open]
        found.append(
            {
                "sys_id": str(a.sys_id),
                "title": a.title,
                "state": a.state,
                "step": a.step,
                "steps": [roles.get(str(s["role"]), str(s["role"])) for s in a.steps],
                "requested_by": names.get(a.requested_by, ""),
                "created": a.created.isoformat(),
                "decided": a.decided.isoformat() if a.decided else None,
                "note": a.note,
                "approvers": [
                    {
                        "name": names.get(r.person, ""),
                        "for": names.get(r.on_behalf_of) if r.on_behalf_of else None,
                    }
                    for r in waiting
                ],
                "decisions": [
                    {
                        "step": d.step,
                        "name": names.get(d.person, ""),
                        "for": names.get(d.on_behalf_of) if d.on_behalf_of else None,
                        "decision": d.decision,
                        "note": d.note,
                        "when": d.created.isoformat(),
                    }
                    for d in decisions
                    if d.approval == a.sys_id
                ],
                "may_decide": any(r.person == me for r in waiting),
                "may_withdraw": a.state == "pending" and a.requested_by == me,
            }
        )
    return found


async def _role_labels(conn: AsyncConnection, language: str) -> dict[str, str]:
    await switch_role(conn, "sys")
    rows = await conn.execute(text("SELECT name, label FROM sys.role WHERE deleted IS NULL"))
    return {r.name: str(translated(r.label, language) or r.name) for r in rows}


async def mine(records: Records) -> list[dict[str, Any]]:
    """What waits for the caller's approval, oldest first."""
    conn = records.conn
    await switch_role(conn, "sys")
    rows = list(
        await conn.execute(
            text(
                'SELECT a.sys_id, a.title, a."table", a.record, a.requested_by, a.step_started,'
                " r.on_behalf_of FROM sys.approver r JOIN sys.approval a ON a.sys_id = r.approval"
                " WHERE r.person = :p AND r.open AND r.deleted IS NULL AND a.state = 'pending'"
                " AND a.deleted IS NULL ORDER BY a.step_started"
            ),
            {"p": current().person_id},
        )
    )
    names = await _names(
        records,
        {r.requested_by for r in rows} | {r.on_behalf_of for r in rows if r.on_behalf_of},
    )
    return [
        {
            "sys_id": str(r.sys_id),
            "title": r.title,
            "link": f"/form/{r.table}/{r.record}",
            "requested_by": names.get(r.requested_by, ""),
            "for": names.get(r.on_behalf_of) if r.on_behalf_of else None,
            "since": r.step_started.isoformat(),
        }
        for r in rows
    ]


# ---- reminders and escalation ----


async def follow_up(conn: AsyncConnection, now: datetime) -> tuple[int, int]:
    """Reminds the approvers of approvals waiting longer than their rule's days, once, and gives
    those waiting longer still to the rule's escalation role (or the administrators)."""
    await switch_role(conn, "sys")
    rows = list(
        await conn.execute(
            text(
                'SELECT a.sys_id, a.title, a."table", a.record, a.requested_by, a.step,'
                " a.step_started, a.reminded, a.escalated, r.remind_after, r.escalate_after,"
                " r.escalate_to FROM sys.approval a JOIN sys.approval_rule r ON r.sys_id = a.rule"
                " WHERE a.state = 'pending' AND a.deleted IS NULL"
            )
        )
    )
    reminded = escalated = 0
    for a in rows:
        waited = now - a.step_started
        if a.escalate_after and not a.escalated and waited >= timedelta(days=a.escalate_after):
            people = (
                await _holders(conn, a.escalate_to)
                if a.escalate_to
                else await _administrators(conn)
            )
            added = await _add_approvers(
                conn, a.sys_id, a.step, people, a.requested_by, escalation=True
            )
            await conn.execute(
                text("UPDATE sys.approval SET escalated = true WHERE sys_id = :a"), {"a": a.sys_id}
            )
            await _tell(
                conn, added, f"Waiting too long, your approval: {a.title}", a.table, a.record
            )
            escalated += 1
        elif a.remind_after and a.reminded is None and waited >= timedelta(days=a.remind_after):
            waiting = list(
                await conn.scalars(
                    text(
                        "SELECT DISTINCT person FROM sys.approver WHERE approval = :a AND open"
                        " AND deleted IS NULL"
                    ),
                    {"a": a.sys_id},
                )
            )
            await conn.execute(
                text("UPDATE sys.approval SET reminded = now() WHERE sys_id = :a"), {"a": a.sys_id}
            )
            await _tell(
                conn, waiting, f"Still waiting for your approval: {a.title}", a.table, a.record
            )
            reminded += 1
    return reminded, escalated


# ---- holding records while they wait ----


async def _hold_while_pending(change: Change) -> None:
    """A record waiting for approval doesn't change (but by the system, which makes the move)."""
    if change.operation == "create" or change.sys_id is None or change.records.access.unrestricted:
        return
    if change.table.key.startswith("sys.approv") or change.table.key == "sys.notification":
        return
    found = await pending(change.records.conn, change.table.key, change.sys_id)
    await switch_role(change.records.conn, change.table.schema)
    if found is not None:
        raise Problem(
            409, "pending", "It's waiting for approval: it can't change until that's decided."
        )


async def _changed_by_admin(change: Change) -> None:
    """A rule an administrator changed is theirs: module updates leave it as it is."""
    if change.operation == "write" and not change.records.access.unrestricted:
        change.values["changed"] = True


async def _check_deputy(change: Change) -> None:
    if change.operation not in ("create", "write"):
        return
    values = change.values
    if values.get("deputy") is not None and str(values.get("deputy")) == str(
        values.get("person", change.person)
    ):
        raise Problem(
            422,
            "validation",
            "Some fields aren't valid.",
            errors=[FieldError("deputy", "invalid", "Someone else stands in for you.")],
        )
    starts, ends = values.get("starts"), values.get("ends")
    if isinstance(starts, date) and isinstance(ends, date) and ends < starts:
        raise Problem(
            422,
            "validation",
            "Some fields aren't valid.",
            errors=[FieldError("ends", "invalid", "Ends on or after it starts.")],
        )


TABLE_HOOKS = (
    TableHook(EVERY_TABLE, before=_hold_while_pending),
    TableHook("sys.approval_rule", before=_changed_by_admin),
    TableHook("sys.deputy", before=_check_deputy),
)
