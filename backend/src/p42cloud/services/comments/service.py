"""Commenting on records, reading the conversation, removing one's own comment.

Rights are the record's: whoever may read it reads its comments and may add one. Only the author
removes a comment, and removing marks it: the conversation's history keeps it. Comments are
plain text; the app never shows them as HTML. Each comment is published (`sys.comment_added`),
so other modules (Tasks) can react.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from p42cloud.data.records import Records
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.services.events.outbox import publish

MAX_TEXT = 5000


@dataclass(frozen=True)
class Said:
    """A comment as the API gives it."""

    sys_id: str
    text: str
    created: str
    created_by: str
    by_name: str | None
    mine: bool


def _allowed(records: Records, table: str) -> None:
    found = records.catalog.table(table)
    if found is None or not found.comments:
        raise Problem(404, "no_comments", "These records have no comments.", detail=table)


def _said(records: Records, row: Any) -> Said:
    return Said(
        str(row.sys_id),
        row.text,
        row.created.isoformat(),
        str(row.created_by),
        row.by_name,
        row.created_by == records.situation.person,
    )


_SELECT = (
    "SELECT c.sys_id, c.text, c.created, c.created_by, p.name AS by_name FROM sys.comment c"
    " LEFT JOIN sys.person p ON p.sys_id = c.created_by"
)


async def listed(records: Records, table: str, record_id: uuid.UUID) -> list[Said]:
    """The record's comments, newest first, for one who may read it."""
    _allowed(records, table)
    await records.read(table, record_id)  # 404 if they may not
    await switch_role(records.conn, "sys")
    rows = await records.conn.execute(
        text(
            f"{_SELECT} WHERE c.record_table = :t AND c.record_id = :r"
            " AND c.deleted IS NULL ORDER BY c.created DESC"
        ),
        {"t": table, "r": str(record_id)},
    )
    return [_said(records, r) for r in rows]


async def add(records: Records, table: str, record_id: uuid.UUID, said: str) -> Said:
    """A comment on a record one may read; in their transaction."""
    _allowed(records, table)
    await records.read(table, record_id)
    words = said.strip()
    if not words:
        raise Problem(422, "validation", "Write something.")
    if len(words) > MAX_TEXT:
        raise Problem(422, "validation", f"A comment is at most {MAX_TEXT} characters.")
    await switch_role(records.conn, "sys")
    made = await records.conn.scalar(
        text(
            "INSERT INTO sys.comment (record_table, record_id, about, text)"
            " VALUES (:t, :r, :about, :text) RETURNING sys_id"
        ),
        {"t": table, "r": str(record_id), "about": record_id, "text": words},
    )
    await publish(
        records.conn, "sys.comment_added", about=(table, record_id), data={"comment": str(made)}
    )
    await switch_role(records.conn, "sys")
    row = (await records.conn.execute(text(f"{_SELECT} WHERE c.sys_id = :i"), {"i": made})).one()
    return _said(records, row)


async def remove(records: Records, comment_id: uuid.UUID) -> None:
    """Removes one's own comment (marked; its history keeps it)."""
    await switch_role(records.conn, "sys")
    row = (
        await records.conn.execute(
            text(
                "SELECT record_table, record_id, created_by FROM sys.comment"
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
    if row.created_by != records.situation.person:
        raise Problem(403, "forbidden", "Only its author removes a comment.")
    await switch_role(records.conn, "sys")
    await records.conn.execute(
        text("UPDATE sys.comment SET deleted = now() WHERE sys_id = :i"), {"i": comment_id}
    )
