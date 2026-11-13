"""The catalog: every table and field, as the metadata records say.

Read from `sys.table`, `sys.field` and `sys.field_type`, never from code. Each process keeps one
in memory and drops it when PostgreSQL announces a change to those tables (`LISTEN`), so every
process sees a change as soon as it's committed.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.types import FIELD_TYPES, Field, FieldType
from p42cloud.kernel.db import transaction

CHANNEL = "p42_metadata"


@dataclass(frozen=True)
class FieldDef:
    name: str
    field: Field
    position: int
    retired: bool = False

    @property
    def type(self) -> FieldType:
        return FIELD_TYPES[self.field.type]


@dataclass(frozen=True)
class TableDef:
    schema: str
    name: str
    label: dict[str, str]
    fields: tuple[FieldDef, ...]
    label_plural: dict[str, str] | None = None
    unique_together: tuple[tuple[str, ...], ...] = ()
    retired: bool = False
    owner_field: str | None = None  # a personal table: the field naming each record's owner
    switched_off: bool = False  # its module is switched off

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.name}"

    def active_fields(self) -> tuple[FieldDef, ...]:
        return tuple(f for f in self.fields if not f.retired)


@dataclass(frozen=True)
class Catalog:
    tables: Mapping[str, TableDef]

    def table(self, key: str) -> TableDef | None:
        return self.tables.get(key)


_TABLES = text(
    'SELECT sys_id, "schema", name, label, label_plural, unique_together, owner_field,'
    " coalesce(switched_off, false) AS switched_off, deleted IS NOT NULL AS retired"
    ' FROM sys."table"'
)
_FIELDS = text(
    'SELECT f."table" AS table_id, f.name, t.name AS type, f.label, f.help, f.options,'
    ' f.required, f."unique", f."default", f.personal, f.position,'
    " f.deleted IS NOT NULL AS retired"
    " FROM sys.field f JOIN sys.field_type t ON t.sys_id = f.type ORDER BY f.position, f.name"
)


def _together(value: Any) -> tuple[tuple[str, ...], ...]:
    groups: list[list[str]] = value or []
    return tuple(tuple(group) for group in groups)


async def load_catalog(conn: AsyncConnection) -> Catalog:
    fields: dict[Any, list[FieldDef]] = {}
    for row in (await conn.execute(_FIELDS)).all():
        declared = Field(
            type=row.type,
            label=row.label,
            options=row.options or {},
            required=row.required,
            unique=row.unique,
            default=row.default,
            help=row.help,
            personal=row.personal,
        )
        fields.setdefault(row.table_id, []).append(
            FieldDef(row.name, declared, row.position, row.retired)
        )
    tables: dict[str, TableDef] = {}
    for row in (await conn.execute(_TABLES)).all():
        table = TableDef(
            schema=row.schema,
            name=row.name,
            label=row.label,
            fields=tuple(fields.get(row.sys_id, [])),
            label_plural=row.label_plural,
            unique_together=_together(row.unique_together),
            retired=row.retired,
            owner_field=row.owner_field,
            switched_off=row.switched_off,
        )
        tables[table.key] = table
    return Catalog(tables)


class MetadataCache:
    """One catalog per process, dropped when the metadata change.

    Without a working `LISTEN` connection it can't know about changes, so it then reads the
    records every time rather than serve an old catalog.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._catalog: Catalog | None = None
        self._generation = 0
        self._listening = False
        self._connection: Any = None
        self._watchers: list[Callable[[], None]] = []

    async def start(self) -> None:
        conn = await self._engine.connect()
        raw: Any = (await conn.get_raw_connection()).driver_connection
        await raw.add_listener(CHANNEL, self._on_notify)
        raw.add_termination_listener(self._on_lost)
        self._connection, self._listening = conn, True
        self._drop()  # anything changed before we listened

    async def stop(self) -> None:
        self._listening = False
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    def on_change(self, watcher: Callable[[], None]) -> None:
        self._watchers.append(watcher)

    async def catalog(self) -> Catalog:
        if self._catalog is not None and self._listening:
            return self._catalog
        generation = self._generation
        async with transaction(self._engine, "sys") as conn:
            catalog = await load_catalog(conn)
        # Keep it only if nothing changed while it was read.
        if generation == self._generation:
            self._catalog = catalog
        return catalog

    def _drop(self) -> None:
        self._catalog = None
        self._generation += 1
        for watcher in self._watchers:
            watcher()

    def _on_notify(self, *_: object) -> None:
        self._drop()

    def _on_lost(self, *_: object) -> None:
        self._listening = False
        self._drop()
