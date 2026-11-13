"""The generic records service: read, list, create, change, delete and restore any table's records.

Works from the catalog only, for every table alike, and as the role of the table's module, so a
module's records are written by that module's role. What the person may read and change comes
from the access in the context (access.py): records out of reach answer 404, a table they may
not use at all 403, and fields they may not read are absent. Values are checked by their field
types; the database checks them again and its refusals come back as field errors. Changes need
the ETag of the version they were made on. Nothing here knows HTTP; the API is a thin layer over
it.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.access import Rules, forbidden
from p42cloud.data.catalog import Catalog, FieldDef, TableDef
from p42cloud.data.conditions import SYSTEM_FIELDS, Filter, Query, Situation, column, field_of
from p42cloud.data.hooks import HOOKS, Change
from p42cloud.data.paging import Sort, after_cursor, cursor
from p42cloud.data.schema import METADATA_TABLES
from p42cloud.data.sql import ident, qualified
from p42cloud.data.types import Invalid
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem

DEFAULT_LIMIT, MAX_LIMIT = 50, 500
MAX_SORT = 5
EXACT_TOTAL = 10_000


@dataclass(frozen=True)
class Record:
    values: dict[str, Any]
    etag: str

    @property
    def sys_id(self) -> UUID:
        return self.values["sys_id"]


@dataclass(frozen=True)
class Page:
    records: list[Record]
    next: str | None  # the cursor of the following page; None on the last one
    total: int | None = None  # None if not counted
    total_exact: bool = True  # False: more than EXACT_TOTAL


def _invalid(errors: list[FieldError]) -> Problem:
    return Problem(422, "validation", "Some fields aren't valid.", errors=errors)


def _not_found(table: TableDef) -> Problem:
    return Problem(404, "not_found", "There's no such record.", detail=table.key)


def refusal(error: DBAPIError) -> Problem | None:
    """A refusal of the database as a field error; None if it isn't one."""
    cause = error.orig.__cause__ if error.orig is not None else None
    state = getattr(cause, "sqlstate", None)
    name = str(getattr(cause, "constraint_name", None) or "")
    if not name.startswith("p42_"):
        return None
    where, kind = name.removeprefix("p42_").rsplit("_", 1)
    found = {
        ("23505", "u"): ("not_unique", "Another active record already has this value."),
        ("23503", "f"): ("no_such_record", "There's no record with this ID."),
        ("23514", "r"): ("required", "This field is required."),
        ("23514", "c"): ("invalid", "This value isn't allowed here."),
    }.get((str(state), kind[:1]))
    if found is None:
        return None
    return _invalid([FieldError(where, *found)])


class Records:
    """The records of every table, for one transaction and the person in its context."""

    def __init__(
        self, conn: AsyncConnection, catalog: Catalog, situation: Situation | None = None
    ) -> None:
        self.conn = conn
        self.catalog = catalog
        context = current()
        self.access = context.access
        self.situation = situation or Situation(
            context.person_id, context.language, context.access.groups, zone=ZoneInfo(context.zone)
        )

    def _rules(self, query: Query) -> Rules:
        return Rules(self.catalog, self.situation, self.access, query)

    # ---- tables and rows ----

    async def _table(self, key: str, *, write: bool = False) -> TableDef:
        table = self.catalog.table(key)
        if table is None or table.switched_off:  # a module switched off: as if not there
            raise Problem(404, "no_table", "There's no such table.", detail=key)
        if write and table.key in METADATA_TABLES:
            raise Problem(
                409,
                "declared_only",
                "Tables and fields change through declarations, not as records.",
                detail=key,
            )
        if write and table.retired:
            raise Problem(409, "retired", "The table is retired; its records are read-only.")
        await switch_role(self.conn, table.schema)
        return table

    def _fields(self, table: TableDef) -> dict[str, FieldDef]:
        system = {n: FieldDef(n, f, -1) for n, f in SYSTEM_FIELDS.items()}
        return system | {f.name: f for f in table.active_fields()}

    def _select(self, table: TableDef, rules: Rules) -> str:
        """Every field they may read on a record; the others come back empty and flagged."""
        columns = [f"t.{ident(n)}" for n in SYSTEM_FIELDS]
        for f in table.active_fields():
            if f.type.secret:
                continue  # written, never read back
            readable = rules.field(table, "t", f.name)
            if readable == "true":
                columns.append(f"t.{ident(f.name)}")
            elif readable != "false":
                columns.append(
                    f"CASE WHEN {readable} THEN t.{ident(f.name)} END AS {ident(f.name)}"
                )
                # Field names start with a letter, so this name can't be a field's.
                columns.append(f'{readable} AS "_can_{f.name}"')
        return f"SELECT {', '.join(columns)} FROM {qualified(table.schema, table.name)} t"  # noqa: S608

    def _record(self, table: TableDef, row: Any) -> Record:
        # By key: a field may be called like a method of the row (count, index).
        found = row._mapping
        values = {
            name: found[name]
            for name in self._fields(table)
            if name in found and found.get(f"_can_{name}", True)
        }
        return Record(values, self._etag(table, values))

    def _etag(self, table: TableDef, values: dict[str, Any]) -> str:
        fields = self._fields(table)
        shown = {name: fields[name].type.to_json(value) for name, value in values.items()}
        canonical = json.dumps(shown, sort_keys=True, default=str)
        return '"' + hashlib.sha256(canonical.encode()).hexdigest()[:16] + '"'

    async def _row(self, table: TableDef, sys_id: UUID, *, lock: bool = False) -> Record:
        """One record they may read: 404 if out of reach, 403 if the table is closed to them."""
        query = Query()
        rules = self._rules(query)
        if not rules.allows(table, "read"):
            raise forbidden(table.key)
        sql = (
            f"{self._select(table, rules)} WHERE t.sys_id = {query.bind(sys_id)}"
            f" AND {rules.rows(table, 't')}{' FOR UPDATE' if lock else ''}"
        )
        row = (await self.conn.execute(text(sql), query.params)).first()
        if row is None:
            raise _not_found(table)
        return self._record(table, row)

    async def _check(
        self, table: TableDef, sys_id: UUID, operation: str, fields: list[str]
    ) -> None:
        """Refuses unless the operation is allowed on this record and on each of `fields`."""
        query = Query()
        rules = self._rules(query)
        if not rules.allows(table, operation):
            raise forbidden(table.key)
        columns = [f"{rules.rows(table, 't', operation)} AS ok"] + [
            f'{rules.field(table, "t", n, operation)} AS "_can_{n}"' for n in fields
        ]
        row: Any = (
            await self.conn.execute(
                text(
                    f"SELECT {', '.join(columns)} FROM {qualified(table.schema, table.name)} t"  # noqa: S608
                    f" WHERE t.sys_id = {query.bind(sys_id)}"
                ),
                query.params,
            )
        ).first()
        if row is None or not row.ok:
            raise forbidden(table.key)
        denied = [n for n in fields if not row._mapping[f"_can_{n}"]]
        if denied:
            raise Problem(
                403,
                "forbidden",
                "You may not change some of these fields.",
                errors=[
                    FieldError(n, "read_only", "You may not change this field.") for n in denied
                ],
            )

    async def may(
        self, table: TableDef, ids: list[UUID], operations: tuple[str, ...]
    ) -> dict[UUID, dict[str, bool]]:
        """For each record, whether the person may do each operation on it (one query)."""
        if not ids:
            return {}
        await switch_role(self.conn, table.schema)
        query = Query()
        rules = self._rules(query)
        columns = ", ".join(
            f'{rules.rows(table, "t", op)} AS "{op}"' for op in operations if op.isalpha()
        )
        rows = await self.conn.execute(
            text(
                f"SELECT t.sys_id, {columns} FROM {qualified(table.schema, table.name)} t"  # noqa: S608
                f" WHERE t.sys_id = ANY({query.bind(ids)})"
            ),
            query.params,
        )
        found: dict[UUID, dict[str, bool]] = {}
        for row in rows:
            values: Any = row._mapping  # pyright: ignore[reportPrivateUsage] - public in SQLAlchemy
            found[values["sys_id"]] = {op: bool(values[op]) for op in operations}
        return found

    async def allowed(
        self, key: str, operation: str, sys_id: UUID, fields: list[str] | None = None
    ) -> bool:
        """Whether they may see the record and do the operation on it (and on `fields`)."""
        table = await self._table(key)
        try:
            await self._row(table, sys_id)
            await self._check(table, sys_id, operation, fields or [])
        except Problem:
            return False
        return True

    async def _write(self, sql: str, params: dict[str, Any]) -> Any:
        try:
            result = await self.conn.execute(text(sql), params)
            return result.first() if result.returns_rows else None
        except DBAPIError as error:
            problem = refusal(error)
            if problem is None:
                raise
            raise problem from error

    def _misnamed(self, table: TableDef, given: dict[str, Any]) -> list[FieldError]:
        active = {f.name for f in table.active_fields()}
        retired = {f.name for f in table.fields if f.retired}
        errors: list[FieldError] = []
        for name in given:
            if name in SYSTEM_FIELDS:
                errors.append(FieldError(name, "read_only", "Set by the database."))
            elif name in retired:
                errors.append(FieldError(name, "read_only", "The field is retired."))
            elif name not in active:
                errors.append(FieldError(name, "unknown_field", "There's no such field."))
        return errors

    def _checked(self, table: TableDef, given: dict[str, Any], *, creating: bool) -> dict[str, Any]:
        """The values to write, each checked by its type; every problem at once.

        Creating fills in defaults and needs every required field.
        """
        errors = self._misnamed(table, given)
        wanted = dict(given)
        if creating:
            for f in table.active_fields():
                if f.name not in wanted and f.field.default is not None:
                    wanted[f.name] = f.field.default
        checked: dict[str, Any] = {}
        for f in table.active_fields():
            if f.name in wanted:
                try:
                    checked[f.name] = f.type.check(wanted[f.name], f.field.options)
                except Invalid as error:
                    errors.append(FieldError(f.name, "invalid", str(error)))
                    continue
            missing = checked.get(f.name) is None and (creating or f.name in checked)
            if f.field.required and missing:
                errors.append(FieldError(f.name, "required", "This field is required."))
        if errors:
            raise _invalid(errors)
        return checked

    def _bind(self, table: TableDef, values: dict[str, Any], query: Query) -> dict[str, str]:
        """Each value as a bound parameter; JSON values cast to jsonb."""
        fields = {f.name: f for f in table.active_fields()}
        bound: dict[str, str] = {}
        for name, value in values.items():
            f = fields[name]
            if f.type.column_type(f.field.options) == "jsonb":
                encoded = None if value is None else json.dumps(value)
                bound[name] = f"CAST({query.bind(encoded)} AS jsonb)"
            else:
                bound[name] = query.bind(value)
        return bound

    def _fresh(self, table: TableDef, record: Record, if_match: str | None) -> None:
        if if_match is None:
            raise Problem(428, "etag_required", "Send the version you changed (If-Match).")
        if if_match != record.etag:
            raise Problem(
                412,
                "changed_since",
                "Someone changed this record in the meantime. Reload it and try again.",
                extra={"current": self.to_json(table, record)},
            )

    def to_json(self, table: TableDef, record: Record) -> dict[str, Any]:
        """A record as JSON: exact numbers and times as text; hidden fields absent."""
        fields = self._fields(table)
        shown = {name: fields[name].type.to_json(v) for name, v in record.values.items()}
        return shown | {"_etag": record.etag}

    # ---- the operations ----

    async def read(self, key: str, sys_id: UUID) -> Record:
        return await self._row(await self._table(key), sys_id)

    async def create(self, key: str, values: dict[str, Any]) -> Record:
        """A new record; the fields given must be ones they may set on such a record.

        If they may not read what they made, only its ID comes back.
        """
        table = await self._table(key, write=True)
        if not self.access.of(table.key, "create"):
            raise forbidden(table.key)
        change = Change(self, table, "create", None, dict(values))
        await HOOKS.run("before", change)
        checked = self._checked(table, change.values, creating=True)
        query = Query()
        bound = self._bind(table, checked, query)
        target = qualified(table.schema, table.name)
        if bound:
            names = ", ".join(ident(n) for n in bound)
            values_sql = f"({names}) VALUES ({', '.join(bound.values())})"
        else:
            values_sql = "DEFAULT VALUES"
        # In a savepoint: a record they may not make is undone before anything else sees it.
        async with self.conn.begin_nested():
            row = await self._write(
                f"INSERT INTO {target} {values_sql} RETURNING sys_id", query.params
            )
            # Only what they sent needs their rights; what hooks set is the system's.
            await self._check(table, row.sys_id, "create", [n for n in values if n in checked])
            change.sys_id = row.sys_id
            await HOOKS.run("after", change)
        try:
            return await self._row(table, row.sys_id)
        except Problem:
            return Record({"sys_id": row.sys_id}, '""')

    async def update(
        self, key: str, sys_id: UUID, values: dict[str, Any], if_match: str | None
    ) -> Record:
        table = await self._table(key, write=True)
        record = await self._row(table, sys_id, lock=True)
        self._fresh(table, record, if_match)
        if record.values["deleted"] is not None:
            raise Problem(409, "deleted", "The record is deleted; restore it first.")
        change = Change(self, table, "write", sys_id, dict(values))
        await HOOKS.run("before", change)
        checked = self._checked(table, change.values, creating=False)
        if not checked:
            return record
        await self._check(table, sys_id, "write", [n for n in values if n in checked])
        query = Query()
        bound = self._bind(table, checked, query)
        changes = ", ".join(f"{ident(n)} = {b}" for n, b in bound.items())
        await self._write(
            f"UPDATE {qualified(table.schema, table.name)} SET {changes}"  # noqa: S608
            f" WHERE sys_id = {query.bind(sys_id)}",
            query.params,
        )
        await HOOKS.run("after", change)
        return await self._row(table, sys_id)

    async def delete(self, key: str, sys_id: UUID, if_match: str | None) -> Record:
        """Soft deletion: the record stays, marked deleted (who and when set by the database)."""
        table = await self._table(key, write=True)
        record = await self._row(table, sys_id, lock=True)
        self._fresh(table, record, if_match)
        if record.values["deleted"] is not None:
            raise Problem(409, "deleted", "The record is already deleted.")
        await self._check(table, sys_id, "delete", [])
        change = Change(self, table, "delete", sys_id)
        await HOOKS.run("before", change)
        await self._write(
            f"UPDATE {qualified(table.schema, table.name)} SET deleted = now()"  # noqa: S608
            " WHERE sys_id = :id",
            {"id": sys_id},
        )
        await HOOKS.run("after", change)
        return await self._row(table, sys_id)

    async def restore(self, key: str, sys_id: UUID) -> Record:
        table = await self._table(key, write=True)
        record = await self._row(table, sys_id, lock=True)
        if record.values["deleted"] is None:
            raise Problem(409, "not_deleted", "The record isn't deleted.")
        await self._check(table, sys_id, "restore", [])
        change = Change(self, table, "restore", sys_id)
        await HOOKS.run("before", change)
        await self._write(
            f"UPDATE {qualified(table.schema, table.name)} SET deleted = NULL"  # noqa: S608
            " WHERE sys_id = :id",
            {"id": sys_id},
        )
        await HOOKS.run("after", change)
        return await self._row(table, sys_id)

    # ---- lists ----

    def _sorting(self, table: TableDef, sort: list[str], query: Query, rules: Rules) -> list[Sort]:
        if len(sort) > MAX_SORT:
            raise _invalid([FieldError("sort", "too_many", f"Sort by at most {MAX_SORT} fields.")])
        found: list[Sort] = []
        for item in sort:
            name = item.removeprefix("-")
            f = field_of(table, name)
            if f is None or f.type.kind is None:
                raise _invalid([FieldError("sort", "not_sortable", f"Can't sort by {name!r}.")])
            expression = column("t", f, query, self.situation)
            readable = rules.field(table, "t", name)
            if readable != "true":  # a field they may not read sorts as empty
                expression = f"(CASE WHEN {readable} THEN {expression} END)"
            found.append(Sort(f, item.startswith("-"), expression))
        return found

    async def list(  # noqa: PLR0913 - the list's options, all by keyword
        self,
        key: str,
        *,
        where: Any = None,
        sort: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
        after: str | None = None,
        count: bool = True,
        deleted: bool = False,
    ) -> Page:
        """One page of records; `deleted=True` lists only deleted ones (the bin)."""
        table = await self._table(key)
        if not self.access.of(table.key, "read"):
            raise forbidden(table.key)
        if not 1 <= limit <= MAX_LIMIT:
            raise _invalid([FieldError("limit", "out_of_range", f"From 1 to {MAX_LIMIT}.")])
        query = Query()
        rules = self._rules(query)
        order = list(sort or [])
        sorting = self._sorting(table, order, query, rules)
        conditions = [
            rules.rows(table, "t"),
            Filter(self.catalog, self.situation, query, guard=rules).sql(table, "t", where),
            f"t.deleted IS {'NOT NULL' if deleted else 'NULL'}",
        ]
        total, exact = None, True
        if count:
            counted = await self.conn.scalar(
                text(
                    f"SELECT count(*) FROM (SELECT 1 FROM {qualified(table.schema, table.name)} t"  # noqa: S608
                    f" WHERE {' AND '.join(conditions)} LIMIT {EXACT_TOTAL + 1}) found"
                ),
                query.params,
            )
            total, exact = min(int(counted or 0), EXACT_TOTAL), int(counted or 0) <= EXACT_TOTAL
        if after:
            conditions.append(after_cursor(after, order, sorting, query))
        ordering = [
            f"{s.expression} {'DESC' if s.descending else 'ASC'} NULLS LAST" for s in sorting
        ]
        sql = (
            f"{self._select(table, rules)} WHERE {' AND '.join(conditions)}"
            f" ORDER BY {', '.join([*ordering, 't.sys_id ASC'])} LIMIT {query.bind(limit + 1)}"
        )
        rows = (await self.conn.execute(text(sql), query.params)).all()
        records = [self._record(table, row) for row in rows[:limit]]
        following = (
            cursor(order, sorting, records[-1].values, self.situation.language)
            if len(rows) > limit
            else None
        )
        return Page(records, following, total, exact)
