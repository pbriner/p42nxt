"""The in-process API client: what a module uses to read and change any module's records.

The same operations, rules and errors as the HTTP API, without the network: records come and go
as JSON (exact numbers and times as text), the person making the request is the one acting, and
the request's transaction is shared. A module never writes another module's tables any other way.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx

from p42cloud.data.catalog import TableDef
from p42cloud.data.hooks import Change
from p42cloud.data.records import Record, Records
from p42cloud.data.settings import DECLARED
from p42cloud.data.settings import value as settings_value
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.services.calls.service import record
from p42cloud.services.connections.service import connect
from p42cloud.services.notifications.service import send
from p42cloud.services.routes.models import RouteRequest
from p42cloud.ui.actions.service import run

type Json = dict[str, Any]


class Client:
    def __init__(self, records: Records) -> None:
        self._records = records

    @classmethod
    def of(cls, call: Change | RouteRequest) -> Client:
        """The client for the request a hook or a module route runs in: same person, same
        transaction."""
        return cls(call.records)

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

    async def setting(self, name: str) -> Any:
        """A setting's value (its default if never changed): public settings, or any the caller
        may read."""
        found = DECLARED.get(name)
        if found is None:
            raise Problem(404, "no_setting", "There's no such setting.", detail=name)
        if not (found.public or self._records.access.of("sys.setting", "read")):
            raise Problem(403, "forbidden", "You may not read this setting.")
        await switch_role(self._records.conn, "sys")
        return await settings_value(self._records.conn, name)

    async def notify(  # noqa: PLR0913 - what a notification says, all by keyword
        self,
        kind: str,
        title: str,
        *,
        people: Sequence[UUID | str] = (),
        roles: Sequence[str] = (),
        message: str | None = None,
        link: str | None = None,
        severity: str = "info",
    ) -> int:
        """Notifies the people and the roles' holders with this request's change (undone with
        it); returns how many people were notified."""
        return await send(
            self._records.conn,
            kind,
            title,
            people=[self._id(p) for p in people],
            roles=roles,
            message=message,
            link=link,
            severity=severity,
        )

    async def call(
        self,
        name: str,
        payload: Json,
        *,
        owner: UUID | str | None = None,
        about: tuple[str, UUID | str] | None = None,
    ) -> UUID:
        """Records an outside call with this request's change: the worker makes it later,
        through the owner's connection, at most once (asking first if an answer was lost)."""
        who = self._id(owner) if owner is not None else None
        return await record(self._records, name, payload, owner=who, about=about)

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
