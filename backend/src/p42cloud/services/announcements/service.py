"""Messages: checked when people write them, and written by the platform (the updater).

A message is shown to everyone signed in while it's valid; an information can be closed by each
person, a warning or a critical message stays while it's valid.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.hooks import Change, TableHook
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.services.announcements.models import AS_NOTIFICATION
from p42cloud.services.notifications.service import send

UPDATING_FOR = timedelta(minutes=30)  # the longest an update's message stays up
SEVERITY = {"information": "info", "warning": "warning", "critical": "error"}
SET_BY_PLATFORM = frozenset({"about_update", "notified"})


async def _check(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values.setdefault("valid_from", datetime.now(UTC))
    if SET_BY_PLATFORM & set(change.values):
        raise Problem(409, "set_by_updater", "The platform sets that itself.")
    start = change.values.get("valid_from")
    end = change.values.get("valid_until")
    if change.sys_id is not None and ("valid_from" in change.values) != (
        "valid_until" in change.values
    ):
        row = (
            await change.records.conn.execute(
                text("SELECT valid_from, valid_until FROM sys.announcement WHERE sys_id = :i"),
                {"i": change.sys_id},
            )
        ).first()
        if row is not None:
            start = start if "valid_from" in change.values else row.valid_from
            end = end if "valid_until" in change.values else row.valid_until
    if start is not None and end is not None and _moment(end) <= _moment(start):
        raise Problem(
            422,
            "validation",
            "Some fields aren't valid.",
            errors=[FieldError("valid_until", "invalid", "After it's shown from.")],
        )


def _moment(value: object) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


async def _send_now(change: Change) -> None:
    """A message to be notified that is valid already goes out with its change."""
    if change.sys_id is not None and change.operation in ("create", "write"):
        await notify(change.records.conn, change.sys_id)


HOOKS = (TableHook("sys.announcement", before=_check, after=_send_now),)

_DUE = (
    "SELECT sys_id, kind, subject, text, audience FROM sys.announcement"
    # The clock, not now(): that's when the transaction began, before "now" was given.
    " WHERE notified IS NULL AND deliver = ANY(:d) AND deleted IS NULL"
    " AND valid_from <= clock_timestamp()"
    " AND (valid_until IS NULL OR valid_until > now())"
)


async def notify(conn: AsyncConnection, message: uuid.UUID | None = None) -> int:
    """Sends the due messages' notifications (one, or all due): once each, to the audience;
    returns how many messages went out. Rows being sent elsewhere are skipped."""
    rows = (
        await conn.execute(
            text(
                _DUE
                + (" AND sys_id = :i" if message else " ORDER BY valid_from LIMIT 20")
                + " FOR UPDATE SKIP LOCKED"
            ),
            {"d": list(AS_NOTIFICATION), "i": message},
        )
    ).all()
    for row in rows:
        await send(
            conn,
            "sys.message",
            row.subject,
            groups=() if row.audience is None else (row.audience,),
            everyone=row.audience is None,
            message=(row.text or None) and str(row.text)[:2000],
            link=f"/form/sys.announcement/{row.sys_id}",
            severity=SEVERITY.get(row.kind, "info"),
        )
        await conn.execute(
            text("UPDATE sys.announcement SET notified = now() WHERE sys_id = :i"),
            {"i": row.sys_id},
        )
    return len(rows)


async def notify_due(engine: AsyncEngine) -> int:
    """The worker's part: messages whose time has come since they were written."""
    with working_as(Context(channel="job:sys.messages", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            return await notify(conn)


async def post(  # noqa: PLR0913 - a message, in full
    conn: AsyncConnection,
    *,
    kind: str,
    subject: str,
    message: str,
    until: datetime,
    update: uuid.UUID,
    start: datetime | None = None,
) -> None:
    """A message the platform writes about an update (as the owner or the system)."""
    await conn.execute(
        text(
            "INSERT INTO sys.announcement (kind, subject, text, valid_from, valid_until,"
            " about_update, deliver)"
            " VALUES (:k, :s, :t, :f, :u, :r, 'band')"
        ),
        {
            "k": kind,
            "s": subject,
            "t": message,
            "f": start or datetime.now(UTC),
            "u": until,
            "r": update,
        },
    )


async def end(conn: AsyncConnection, update: uuid.UUID) -> None:
    """The update's messages end now (it ran, or was cancelled)."""
    await conn.execute(
        text(
            "UPDATE sys.announcement SET valid_until = now()"
            " WHERE about_update = :r AND (valid_until IS NULL OR valid_until > now())"
            " AND deleted IS NULL"
        ),
        {"r": update},
    )
