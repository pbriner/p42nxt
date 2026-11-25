"""Commenting on records, reading the conversation, removing one's own comment or work note.

Rights are the record's: whoever may read it reads its comments and may add one; work notes are
read and added only by those who may change it. Only the author removes either, and removing
marks it: the conversation's history keeps it. Both are plain text; the app never shows them as
HTML. Each is published (`sys.comment_added`, `sys.work_note_added`), so other modules (Tasks)
can react; a work note never goes out as a comment, so nothing listening for comments passes
one on.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from p42cloud.data.catalog import TableDef
from p42cloud.data.records import Records
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.services.events.outbox import publish

MAX_TEXT = 5000
COMMENT, WORK_NOTE = "comment", "work_note"
EVENTS = {COMMENT: "sys.comment_added", WORK_NOTE: "sys.work_note_added"}


@dataclass(frozen=True)
class Said:
    """A comment as the API gives it."""

    sys_id: str
    text: str
    created: str
    created_by: str
    by_name: str | None
    mine: bool
    kind: str = COMMENT


def _conversation(records: Records, table: str) -> TableDef:
    found = records.catalog.table(table)
    if found is None or not (found.comments or found.work_notes):
        raise Problem(404, "no_comments", "These records have no comments.", detail=table)
    return found


async def _may_change(records: Records, table: TableDef, record_id: uuid.UUID) -> bool:
    allowed = await records.may(table, [record_id], ("write",))
    return bool(allowed.get(record_id, {}).get("write"))


async def _kinds(records: Records, table: TableDef, record_id: uuid.UUID) -> set[str]:
    """What the person reads and adds on this record (they may read it)."""
    kinds = {COMMENT} if table.comments else set[str]()
    if table.work_notes and await _may_change(records, table, record_id):
        kinds.add(WORK_NOTE)
    return kinds


def _said(records: Records, row: Any) -> Said:
    return Said(
        str(row.sys_id),
        row.text,
        row.created.isoformat(),
        str(row.created_by),
        row.by_name,
        row.created_by == records.situation.person,
        row.kind,
    )


_SELECT = (
    "SELECT c.sys_id, c.text, c.created, c.created_by, c.kind, p.name AS by_name"
    " FROM sys.comment c LEFT JOIN sys.person p ON p.sys_id = c.created_by"
)


async def listed(records: Records, table: str, record_id: uuid.UUID) -> list[Said]:
    """The record's comments, and its work notes for one who may change it; newest first."""
    found = _conversation(records, table)
    await records.read(table, record_id)  # 404 if they may not
    kinds = await _kinds(records, found, record_id)
    await switch_role(records.conn, "sys")
    rows = await records.conn.execute(
        text(
            f"{_SELECT} WHERE c.record_table = :t AND c.record_id = :r"
            " AND c.kind = ANY(:kinds) AND c.deleted IS NULL ORDER BY c.sys_id DESC"
        ),
        {"t": table, "r": str(record_id), "kinds": sorted(kinds)},
    )
    return [_said(records, r) for r in rows]


async def add(
    records: Records, table: str, record_id: uuid.UUID, said: str, kind: str = COMMENT
) -> Said:
    """A comment on a record one may read, or a work note on one they may change; in their
    transaction."""
    found = _conversation(records, table)
    await records.read(table, record_id)
    if kind not in EVENTS:
        raise Problem(422, "validation", "A comment or a work note.", detail=kind)
    if kind not in await _kinds(records, found, record_id):
        if kind == WORK_NOTE and found.work_notes:
            raise Problem(403, "forbidden", "Only those who may change the record add work notes.")
        raise Problem(404, "no_comments", f"These records have no {kind.replace('_', ' ')}s.")
    words = said.strip()
    if not words:
        raise Problem(422, "validation", "Write something.")
    if len(words) > MAX_TEXT:
        raise Problem(422, "validation", f"A comment is at most {MAX_TEXT} characters.")
    await switch_role(records.conn, "sys")
    made = await records.conn.scalar(
        text(
            "INSERT INTO sys.comment (record_table, record_id, about, text, kind)"
            " VALUES (:t, :r, :about, :text, :kind) RETURNING sys_id"
        ),
        {"t": table, "r": str(record_id), "about": record_id, "text": words, "kind": kind},
    )
    await publish(records.conn, EVENTS[kind], about=(table, record_id), data={kind: str(made)})
    await switch_role(records.conn, "sys")
    row = (await records.conn.execute(text(f"{_SELECT} WHERE c.sys_id = :i"), {"i": made})).one()
    return _said(records, row)


async def remove(records: Records, comment_id: uuid.UUID) -> None:
    """Removes one's own comment or work note (marked; its history keeps it)."""
    await switch_role(records.conn, "sys")
    row = (
        await records.conn.execute(
            text(
                "SELECT record_table, record_id, created_by, kind FROM sys.comment"
                " WHERE sys_id = :i AND deleted IS NULL"
            ),
            {"i": comment_id},
        )
    ).first()
    if row is None:
        raise Problem(404, "not_found", "There's no such comment.")
    try:
        await records.read(row.record_table, uuid.UUID(row.record_id))
    except Problem as refused:
        raise Problem(404, "not_found", "There's no such comment.") from refused
    table = records.catalog.table(row.record_table)
    if row.kind == WORK_NOTE and (
        table is None or not await _may_change(records, table, uuid.UUID(row.record_id))
    ):
        raise Problem(404, "not_found", "There's no such comment.")  # not theirs to know of
    if row.created_by != records.situation.person:
        raise Problem(403, "forbidden", "Only its author removes a comment.")
    await switch_role(records.conn, "sys")
    await records.conn.execute(
        text("UPDATE sys.comment SET deleted = now() WHERE sys_id = :i"), {"i": comment_id}
    )
