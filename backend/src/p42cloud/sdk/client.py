"""The in-process API client: what a module uses to read and change any module's records.

The same operations, rules and errors as the HTTP API, without the network: records come and go
as JSON (exact numbers and times as text), the person making the request is the one acting, and
the request's transaction is shared. A module never writes another module's tables any other way.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx

from p42cloud.data.catalog import TableDef
from p42cloud.data.hooks import Change
from p42cloud.data.records import Record, Records
from p42cloud.kernel.errors import Problem
from p42cloud.services.connections.service import connect
from p42cloud.ui.actions.service import run

type Json = dict[str, Any]


class Client:
    def __init__(self, records: Records) -> None:
        self._records = records

    @classmethod
    def of(cls, change: Change) -> Client:
        """The client for the request a hook runs in: same person, same transaction."""
        return cls(change.records)

    def _table(self, key: str) -> TableDef:
        table = self._records.catalog.table(key)
        if table is None:
            raise Problem(404, "no_table", "There's no such table.", detail=key)
        return table

    def _json(self, key: str, record: Record) -> Json:
        return self._records.to_json(self._table(key), record)

    @staticmethod
    def _id(sys_id: UUID | str) -> UUID:
        return sys_id if isinstance(sys_id, UUID) else UUID(sys_id)

    async def list(  # noqa: PLR0913 - the list's options, all by keyword
        self,
        table: str,
        *,
        where: Any = None,
        sort: list[str] | None = None,
        limit: int = 50,
        after: str | None = None,
        deleted: bool = False,
    ) -> Json:
        page = await self._records.list(
            table, where=where, sort=sort, limit=limit, after=after, deleted=deleted
        )
        return {
            "records": [self._json(table, r) for r in page.records],
            "next": page.next,
            "total": page.total,
            "total_exact": page.total_exact,
        }

    async def get(self, table: str, sys_id: UUID | str) -> Json:
        return self._json(table, await self._records.read(table, self._id(sys_id)))

    async def create(self, table: str, values: Json) -> Json:
        return self._json(table, await self._records.create(table, values))

    async def update(self, table: str, sys_id: UUID | str, values: Json, etag: str) -> Json:
        changed = await self._records.update(table, self._id(sys_id), values, etag)
        return self._json(table, changed)

    async def delete(self, table: str, sys_id: UUID | str, etag: str) -> Json:
        return self._json(table, await self._records.delete(table, self._id(sys_id), etag))

    async def restore(self, table: str, sys_id: UUID | str) -> Json:
        return self._json(table, await self._records.restore(table, self._id(sys_id)))

    async def action(
        self, table: str, sys_id: UUID | str, name: str, inputs: Json | None = None
    ) -> Json:
        """Runs a UI action on a record, checked as over HTTP: a message, where to go next."""
        return await run(self._records, table, name, self._id(sys_id), given=inputs or {})

    @asynccontextmanager
    async def connection(
        self, type_name: str, *, owner: UUID | str | None = None, label: str | None = None
    ) -> AsyncGenerator[httpx.AsyncClient]:
        """A ready client for a connection of one of the module's types: the person's own, or
        (in the module's jobs) the one of the person they work for. It knows the address and
        signs in; the module never sees a secret."""
        who = self._id(owner) if owner is not None else None
        async with connect(self._records, type_name, owner=who, label=label) as http:
            yield http
