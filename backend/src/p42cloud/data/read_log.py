"""Reads that are logged: every read with an API token, and every read of a sensitive table
(`Table(log_reads=True)`), as an entry of the audit log (`records_read`): who, through which
channel, which table and which records. Changes are always in the audit log; these are the reads.
"""

import json
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import text

from p42cloud.data.catalog import TableDef
from p42cloud.data.records import Records
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role

MAX_IDS = 100  # named in the entry; past that, only counted
TOKEN_CHANNEL = "api:token:"  # noqa: S105 - a channel's name, not a secret


def logged(table: TableDef) -> bool:
    """Whether reading this table now is logged: with a token, or a sensitive table."""
    return table.log_reads or current().channel.startswith(TOKEN_CHANNEL)


async def log_reads(records: Records, table: TableDef, ids: Iterable[UUID]) -> None:
    """An entry for the records just read, if reads of this table are logged now."""
    if not logged(table):
        return
    found = [str(i) for i in ids]
    if not found:
        return
    details = {"table": table.key, "count": len(found), "records": found[:MAX_IDS]}
    await switch_role(records.conn, "sys")
    await records.conn.execute(
        text("SELECT sys.p42_security_event('records_read', CAST(:details AS jsonb))"),
        {"details": json.dumps(details)},
    )
