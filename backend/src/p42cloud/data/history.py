"""A record's history: its entries of the audit log, as the person may see them.

Only someone who may read the record sees its history, and of each change only the fields they
may read (a secret's change was never logged with its values). Newest first.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text

from p42cloud.data.records import Records
from p42cloud.kernel.db import switch_role

MAX_ENTRIES = 200


async def history(
    records: Records, key: str, sys_id: UUID, limit: int = 100
) -> list[dict[str, Any]]:
    """The record's changes, newest first: when, what, by whom, through which channel."""
    record = await records.read(key, sys_id)  # not theirs to read: not found, as for the record
    readable = set(record.values)
    conn = records.conn
    await switch_role(conn, "sys")
    rows = list(
        await conn.execute(
            text(
                "SELECT created, operation, person_id, channel, changes FROM sys.audit_log"
                " WHERE table_name = :t AND record_id = :id ORDER BY sys_id DESC LIMIT :n"
            ),
            {"t": key, "id": sys_id, "n": max(1, min(limit, MAX_ENTRIES))},
        )
    )
    people = {
        row.sys_id: row.name
        for row in await conn.execute(
            text("SELECT sys_id, name FROM sys.person WHERE sys_id = ANY(:ids)"),
            {"ids": list({r.person_id for r in rows})},
        )
    }
    return [
        {
            "at": r.created.isoformat(),
            "operation": r.operation,
            "by": str(r.person_id),
            "by_name": people.get(r.person_id),
            "channel": r.channel,
            "changes": {k: v for k, v in dict(r.changes).items() if k in readable},
        }
        for r in rows
    ]
