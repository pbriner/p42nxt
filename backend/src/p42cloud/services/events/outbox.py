"""Publishing an event: written with the change that caused it, so it exists only if that change
is kept. Apart from the delivery so that any service may publish without depending on it."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem

MAX_DATA = 16_000  # bytes of JSON: events say what happened, the records hold the rest


async def publish(
    conn: AsyncConnection,
    event_type: str,
    *,
    about: tuple[str, uuid.UUID | str] | None = None,
    data: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Publishes an event in the caller's transaction, with a delivery for each module that
    listens for its type; returns the event's ID."""
    try:
        body = json.dumps(data or {})
    except (TypeError, ValueError) as error:
        raise Problem(422, "validation", "An event's data is JSON.") from error
    if len(body.encode()) > MAX_DATA:
        raise Problem(422, "validation", f"An event's data is at most {MAX_DATA} bytes.")
    about_id = uuid.UUID(str(about[1])) if about else None
    await switch_role(conn, "sys")
    module = await conn.scalar(
        text(
            "SELECT t.module FROM sys.event_type t JOIN sys.module m ON m.name = t.module"
            " AND m.deleted IS NULL WHERE t.name = :t AND t.deleted IS NULL"
        ),
        {"t": event_type},
    )
    if module is None:
        raise Problem(404, "no_event_type", "There's no such event type.", detail=event_type)
    made = await conn.scalar(
        text(
            "INSERT INTO sys.event (type, module, record_table, record_id, about, data)"
            " VALUES (:type, :module, :table, :id, :about, CAST(:data AS jsonb))"
            " RETURNING sys_id"
        ),
        {
            "type": event_type,
            "module": module,
            "table": about[0] if about else None,
            "id": str(about_id) if about_id else None,
            "about": about_id,
            "data": body,
        },
    )
    if not isinstance(made, uuid.UUID):
        raise RuntimeError("no event recorded")
    # Removed modules hear nothing; switched-off ones get theirs when switched on again.
    await conn.execute(
        text(
            "INSERT INTO sys.event_delivery (event, subscription, module, state, attempts,"
            " not_before) SELECT :event, s.name, s.module, 'pending', 0, now()"
            " FROM sys.event_subscription s JOIN sys.module m ON m.name = s.module"
            " AND m.deleted IS NULL WHERE s.event_type = :type AND s.deleted IS NULL"
        ),
        {"event": made, "type": event_type},
    )
    return made
