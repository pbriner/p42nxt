"""Security events: audit entries without a record (sign-ins, failures, credential changes)."""

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.kernel.db import switch_role

_RECENT = text(
    "SELECT count(*) FROM sys.audit_log WHERE table_name IS NULL AND operation = :event"
    " AND created > :since AND changes ->> :key = :value"
)


async def event(conn: AsyncConnection, name: str, **details: str | None) -> None:
    await switch_role(conn, "sys")
    await conn.execute(
        text("SELECT sys.p42_security_event(:name, CAST(:details AS jsonb))"),
        {"name": name, "details": json.dumps({k: v for k, v in details.items() if v is not None})},
    )


async def failures(conn: AsyncConnection, name: str, key: str, value: str, since: datetime) -> int:
    """How many `name` events with this detail happened since then."""
    await switch_role(conn, "sys")
    found = await conn.scalar(_RECENT, {"event": name, "key": key, "value": value, "since": since})
    return int(found or 0)
