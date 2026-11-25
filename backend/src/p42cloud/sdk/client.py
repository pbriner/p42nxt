"""The in-process API client: what a module uses to read and change any module's records.

The same operations, rules and errors as the HTTP API, without the network: records come and go
as JSON (exact numbers and times as text), the person making the request is the one acting, and
the request's transaction is shared. A module never writes another module's tables any other way.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date
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
from p42cloud.services.approvals import service as approvals
from p42cloud.services.calls.service import record
from p42cloud.services.comments import service as comments
from p42cloud.services.connections.service import connect
from p42cloud.services.currencies.service import convert
from p42cloud.services.events.outbox import publish
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
        columns: list[str] | None = None,
    ) -> Json:
        """A page of records; `columns` adds fields of referenced records (`partner.city`)."""
        page = await self._records.list(
            table,
            where=where,
            sort=sort,
            limit=limit,
            after=after,
            deleted=deleted,
            columns=columns,
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

    async def delete(
        self, table: str, sys_id: UUID | str, etag: str, confirm: str | None = None
    ) -> Json:
        """Records pointing to it go as their fields declare; `confirm` (a deletion preview's
        fingerprint) makes sure only what was checked changes."""
        return self._json(table, await self._records.delete(table, self._id(sys_id), etag, confirm))

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
        groups: Sequence[UUID | str] = (),
        message: str | None = None,
        link: str | None = None,
        severity: str = "info",
    ) -> int:
        """Notifies the people, the roles' holders and the groups' members with this request's
        change (undone with it); returns how many people were notified."""
        return await send(
            self._records.conn,
            kind,
            title,
            people=[self._id(p) for p in people],
            roles=roles,
            groups=[self._id(g) for g in groups],
            message=message,
            link=link,
            severity=severity,
        )

    async def comment(
        self, table: str, sys_id: UUID | str, text: str, *, kind: str = "comment"
    ) -> Json:
        """Adds a comment (for everyone who may read the record) or a work note (`kind=
        "work_note"`, for those who may change it), checked as over HTTP, with this request's
        change."""
        return asdict(await comments.add(self._records, table, self._id(sys_id), text, kind))

    async def comments(self, table: str, sys_id: UUID | str) -> list[Json]:
        """The record's comments, and its work notes if the caller may change it; newest
        first."""
        return [asdict(c) for c in await comments.listed(self._records, table, self._id(sys_id))]

    async def approvals(self, table: str, sys_id: UUID | str) -> list[Json]:
        """The record's approvals, newest first, with what the caller may do (may_decide)."""
        found = self._records.catalog.table(table)
        if found is None:
            raise Problem(404, "not_found", "There's no such table.")
        return await approvals.of_record(self._records, found, self._id(sys_id))

    async def decide(self, approval: UUID | str, decision: str, note: str | None = None) -> Json:
        """Approve or reject (with a note saying why) as an approver; withdraw as who asked."""
        if decision == "withdraw":
            return await approvals.withdraw(self._records, self._id(approval))
        return await approvals.decide(self._records, self._id(approval), decision, note)

    async def pending_approval(self, table: str, sys_id: UUID | str) -> bool:
        """Whether an approval of a move of this record is waiting (whoever may see it)."""
        return await approvals.pending(self._records.conn, table, self._id(sys_id)) is not None

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

    async def convert(
        self, amount: Any, source: str, target: str, *, on: date | None = None
    ) -> str:
        """The amount in another currency with the rate of the day (today if not given), as
        exact text; refused when there's no rate for the day or the days before."""
        return str(await convert(self._records.conn, amount, source, target, on))

    async def publish(
        self,
        event_type: str,
        *,
        about: tuple[str, UUID | str] | None = None,
        data: Json | None = None,
    ) -> UUID:
        """Publishes an event with this request's change (undone with it): every module that
        listens for its type gets it once, after the change is saved."""
        return await publish(self._records.conn, event_type, about=about, data=data)

    @asynccontextmanager
    async def connection(
        self,
        type_name: str,
        *,
        owner: UUID | str | None = None,
        label: str | None = None,
        connection: UUID | str | None = None,
    ) -> AsyncGenerator[httpx.AsyncClient]:
        """A ready client for a connection of one of the module's types: the person's own, or
        (in the module's jobs) the one of the person they work for; one of theirs in particular
        when `connection` names it (only theirs, and only of the type). It knows the address and
        signs in; the module never sees a secret."""
        who = self._id(owner) if owner is not None else None
        which = self._id(connection) if connection is not None else None
        async with connect(
            self._records, type_name, owner=who, label=label, connection=which
        ) as http:
            yield http
