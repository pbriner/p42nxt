"""Sharing personal records: only a record's owner shares it, changes the share or ends it.

A share names the table and the record; the system notes the owner on it. Which records anyone
then sees is applied by the data layer (data/access.py).
"""

from typing import Any

from sqlalchemy import text

from p42cloud.data.hooks import Change
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem

_TABLE_KEY = text('SELECT "schema" || \'.\' || name FROM sys."table" WHERE sys_id = :id')
_OWNER = text("SELECT sys.p42_owner_of(:table, :record)")


async def share(change: Change) -> None:
    conn = change.records.conn
    await switch_role(conn, "sys")
    before: dict[str, Any] = {}
    if change.sys_id is not None:
        found = (
            await conn.execute(
                text('SELECT "table", record FROM sys.share WHERE sys_id = :id'),
                {"id": change.sys_id},
            )
        ).first()
        before = dict(found._mapping) if found else {}  # pyright: ignore[reportPrivateUsage]
        if {"table", "record", "owner"} & change.values.keys():
            raise Problem(409, "fixed", "A share stays with its record; make a new one instead.")
    table = change.values.get("table", before.get("table"))
    record = change.values.get("record", before.get("record"))
    if table is None or record is None:
        return  # checking the values refuses it
    key = await conn.scalar(_TABLE_KEY, {"id": table})
    owner = await conn.scalar(_OWNER, {"table": key, "record": record})
    if owner is None:
        raise Problem(422, "not_personal", "Only records of personal tables are shared.")
    if not change.records.access.unrestricted and owner != change.records.situation.person:
        raise Problem(403, "not_owner", "Only the record's owner may share it.")
    if change.operation == "create":
        change.values["owner"] = owner
