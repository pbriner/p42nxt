"""Undo: reverses what one audit entry, or everything one request, did.

Undo is a new change, never a rewrite of history. It is audited like any other, under the undo's
own request ID and a channel that names what it undid (`ui:undo:<id>`), and needs the rights of a
change today: undoing a create soft deletes (delete rights), undoing a delete restores (restore
rights), undoing an update sets fields back (write rights on each). A field changed again since
isn't touched: it's listed as skipped with the reason, and so is anything not allowed or refused
by the database.
"""

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.records import Records, refusal
from p42cloud.data.schema import METADATA_TABLES
from p42cloud.data.sql import ident, qualified
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role

# How the audit log marks a secret field: that it changed, without its values.
MASKED = "(changed)"


@dataclass(frozen=True)
class Skipped:
    table: str
    record: UUID
    field: str | None
    reason: str


@dataclass
class Undone:
    changed: list[tuple[str, UUID, str]] = field(default_factory=list[tuple[str, UUID, str]])
    skipped: list[Skipped] = field(default_factory=list[Skipped])


class _Undo:
    def __init__(self, conn: AsyncConnection, catalog: Catalog) -> None:
        self.conn = conn
        self.catalog = catalog
        self.result = Undone()
        self.records = Records(conn, catalog)

    def skip(self, table: str, record: UUID, reason: str, name: str | None = None) -> None:
        self.result.skipped.append(Skipped(table, record, name, reason))

    async def entry(self, row: Any) -> None:
        table = self.catalog.table(row.table_name)
        if table is None or table.retired or table.key in METADATA_TABLES:
            self.skip(row.table_name, row.record_id, "This table's changes can't be undone here.")
            return
        await switch_role(self.conn, table.schema)
        now: dict[str, Any] | None = await self.conn.scalar(
            text(
                f"SELECT to_jsonb(t) FROM {qualified(table.schema, table.name)} t"  # noqa: S608
                " WHERE sys_id = :id FOR UPDATE"
            ),
            {"id": row.record_id},
        )
        if now is None:
            self.skip(table.key, row.record_id, "The record isn't there any more.")
            return
        deleted = now["deleted"] is not None
        match row.operation:
            case "insert" | "restore" if deleted:
                self.skip(table.key, row.record_id, "The record is deleted already.")
            case "insert" | "restore":
                if await self.may(table, row.record_id, "delete"):
                    await self.apply(table, row.record_id, "deleted = now()", {}, "deleted")
            case "delete" if not deleted:
                self.skip(table.key, row.record_id, "The record was restored already.")
            case "delete":
                if await self.may(table, row.record_id, "restore"):
                    await self.apply(table, row.record_id, "deleted = NULL", {}, "restored")
            case _ if deleted:
                self.skip(table.key, row.record_id, "The record is deleted; restore it first.")
            case _:
                await self.fields(table, row.record_id, now, row.changes)

    async def fields(
        self, table: TableDef, record: UUID, now: dict[str, Any], changes: dict[str, Any]
    ) -> None:
        active = {f.name for f in table.active_fields()}
        back: dict[str, Any] = {}
        for name, change in changes.items():
            if change == MASKED:
                self.skip(table.key, record, "A secret's old value isn't kept.", name)
            elif name not in active:
                self.skip(table.key, record, "The field is retired.", name)
            elif now.get(name) != change[1]:
                self.skip(table.key, record, "It was changed again since.", name)
            else:
                back[name] = change[0]
        if not back or not await self.may(table, record, "write", list(back)):
            return
        # The audit log holds the values as the database wrote them, so the database reads them
        # back into the columns' own types.
        names = ", ".join(ident(n) for n in back)
        row_type = qualified(table.schema, table.name)
        assignment = (
            f"({names}) = (SELECT {names} FROM"  # noqa: S608
            f" jsonb_populate_record(NULL::{row_type}, CAST(:back AS jsonb)))"
        )
        await self.apply(
            table, record, assignment, {"back": json.dumps(back)}, f"set back {', '.join(back)}"
        )

    async def may(
        self, table: TableDef, record: UUID, operation: str, fields: list[str] | None = None
    ) -> bool:
        """Whether they may do this today; if not, it's skipped and said why."""
        if await self.records.allowed(table.key, operation, record, fields):
            return True
        self.skip(table.key, record, f"You may not {operation} this record (or these fields).")
        return False

    async def apply(
        self, table: TableDef, record: UUID, assignment: str, params: dict[str, Any], what: str
    ) -> None:
        try:
            async with self.conn.begin_nested():
                await self.conn.execute(
                    text(
                        f"UPDATE {qualified(table.schema, table.name)} SET {assignment}"  # noqa: S608
                        " WHERE sys_id = :id"
                    ),
                    {**params, "id": record},
                )
        except DBAPIError as error:
            # In plain words, without the database's internals.
            problem = refusal(error)
            found = problem.errors[0] if problem and problem.errors else None
            reason = f"{found.field}: {found.message}" if found else "The database refused it."
            self.skip(table.key, record, reason)
            return
        self.result.changed.append((table.key, record, what))


_BY_REQUEST = text(
    "SELECT table_name, record_id, operation, changes FROM sys.audit_log"
    " WHERE request_id = :id AND record_id IS NOT NULL ORDER BY sys_id DESC"
)
_BY_ENTRY = text(
    "SELECT table_name, record_id, operation, changes FROM sys.audit_log"
    " WHERE sys_id = :id AND record_id IS NOT NULL"
)


async def undo(
    conn: AsyncConnection,
    catalog: Catalog,
    *,
    request: UUID | None = None,
    entry: UUID | None = None,
) -> Undone:
    """Undoes everything a request did (newest first), or one audit entry."""
    if (request is None) == (entry is None):
        raise ValueError("undo needs either a request or an entry")
    what = request or entry
    await switch_role(conn, "sys")
    rows = (await conn.execute(_BY_REQUEST if request else _BY_ENTRY, {"id": what})).all()
    channel = f"{current().channel}:undo:{what}"
    await conn.execute(text("SELECT set_config('p42.channel', :c, true)"), {"c": channel})
    work = _Undo(conn, catalog)
    for row in rows:
        await work.entry(row)
    # The rest of the transaction is no longer the undo (a failure above rolls it all back).
    await conn.execute(text("SELECT set_config('p42.channel', :c, true)"), {"c": current().channel})
    return work.result
