"""The metadata tables, and writing declarations into them.

`sys.field_type`, `sys.table` and `sys.field` describe every table, themselves included: they're
declared like any other table, and built by the same engine. Before they exist, the engine builds
them from their declarations; from then on, from their records.
"""

import json
import uuid
from dataclasses import replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table, check, to_catalog
from p42cloud.data.schema import apply
from p42cloud.data.types import FIELD_TYPES, Field
from p42cloud.data.types.base import BEHAVIOUR
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText
from p42cloud.kernel.errors import Problem

CORE = "sys"

METADATA_TABLES = [
    Table(
        "field_type",
        "Field type",
        {
            "name": String("Name", max_length=40, required=True, unique=True),
            "label": TranslatedText("Label", required=True),
        },
        label_plural="Field types",
    ),
    Table(
        "table",
        "Table",
        {
            "schema": String("Module", max_length=16, required=True),
            "name": String("Name", max_length=40, required=True),
            "label": TranslatedText("Label", required=True),
            "label_plural": TranslatedText("Label for several"),
            "unique_together": Json("Unique together"),
            "owner_field": String("Owner field", max_length=40),
            # Its module is switched off: nobody reads or writes it until it's on again.
            "switched_off": Boolean("Switched off", required=True, default=False),
            "display_field": String("Display field", max_length=40),
            # A hierarchy: the field naming each record's parent.
            "parent_field": String("Parent field", max_length=40),
            "files": Boolean("Files can be attached", default=False),
            "comments": Boolean("Comments can be added", default=False),
            "work_notes": Boolean("Work notes can be added", default=False),
        },
        label_plural="Tables",
        unique_together=(("schema", "name"),),
    ),
    Table(
        "field",
        "Field",
        {
            "table": Reference("Table", "sys.table", required=True),
            "name": String("Name", max_length=40, required=True),
            "type": Reference("Type", "sys.field_type", required=True),
            "label": TranslatedText("Label", required=True),
            "help": TranslatedText("Help"),
            "options": Json("Options"),
            "required": Boolean("Required", required=True),
            "unique": Boolean("Unique", required=True),
            "default": Json("Default"),
            "personal": Boolean("Personal data", required=True),
            "position": Integer("Position", required=True),
        },
        label_plural="Fields",
        unique_together=(("table", "name"),),
    ),
]


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


async def sync_field_types(conn: AsyncConnection) -> None:
    """One record per field type this version of the code has; others are retired."""
    existing = {
        name: row_id
        for name, row_id in (await conn.execute(text("SELECT name, sys_id FROM sys.field_type")))
    }
    for name, field_type in sorted(FIELD_TYPES.items()):
        values = {"name": name, "label": _json({"en": field_type.label})}
        if name in existing:
            await conn.execute(
                text(
                    "UPDATE sys.field_type SET label = CAST(:label AS jsonb), deleted = NULL"
                    " WHERE name = :name"
                ),
                values,
            )
        else:
            await conn.execute(
                text(
                    "INSERT INTO sys.field_type (name, label) VALUES (:name, CAST(:label AS jsonb))"
                ),
                values,
            )
    await conn.execute(
        text(
            "UPDATE sys.field_type SET deleted = now()"
            " WHERE deleted IS NULL AND NOT name = ANY(:names)"
        ),
        {"names": sorted(FIELD_TYPES)},
    )


_TABLE_VALUES = (
    '"schema" = :schema, name = :name, label = CAST(:label AS jsonb),'
    " label_plural = CAST(:label_plural AS jsonb),"
    " unique_together = CAST(:unique_together AS jsonb), owner_field = :owner_field,"
    " display_field = :display_field, parent_field = :parent_field, files = :files,"
    " comments = :comments, work_notes = :work_notes"
)
_FIELD_COLUMNS = (
    '"table", name, type, label, help, options, required, "unique", "default", personal, position'
)
_FIELD_PARAMS = (
    ":table, :name, :type, CAST(:label AS jsonb), CAST(:help AS jsonb),"
    " CAST(:options AS jsonb), :required, :unique, CAST(:default AS jsonb), :personal, :position"
)


async def _write_table(conn: AsyncConnection, schema: str, declared: Table) -> uuid.UUID:
    values = {
        "schema": schema,
        "name": declared.name,
        "label": _json({"en": declared.label}),
        "label_plural": _json({"en": declared.label_plural} if declared.label_plural else None),
        "unique_together": _json([list(u) for u in declared.unique_together] or None),
        "owner_field": declared.owner,
        "display_field": declared.display,
        "parent_field": declared.parent,
        "files": declared.files,
        "comments": declared.comments,
        "work_notes": declared.work_notes,
    }
    row_id = await conn.scalar(
        text('SELECT sys_id FROM sys."table" WHERE "schema" = :schema AND name = :name'), values
    )
    if row_id is None:
        row_id = await conn.scalar(
            text(
                'INSERT INTO sys."table"'
                ' ("schema", name, label, label_plural, unique_together, owner_field,'
                " display_field, parent_field, files, comments, work_notes, switched_off)"
                " VALUES (:schema, :name, CAST(:label AS jsonb), CAST(:label_plural AS jsonb),"
                " CAST(:unique_together AS jsonb), :owner_field, :display_field, :parent_field,"
                " :files, :comments, :work_notes, false) RETURNING sys_id"
            ),
            values,
        )
    else:
        await conn.execute(
            # Only the constants above go into the statement; values are bound.
            text(f'UPDATE sys."table" SET {_TABLE_VALUES}, deleted = NULL WHERE sys_id = :id'),  # noqa: S608
            {**values, "id": row_id},
        )
    if not isinstance(row_id, uuid.UUID):
        raise RuntimeError(f"no ID for the table {schema}.{declared.name}")
    return row_id


def _refuse_loss(where: str, old: Field, new: Field) -> None:
    # How the app treats a field (read_only, on_delete) never touches its values.
    old = replace(old, options={k: v for k, v in old.options.items() if k not in BEHAVIOUR})
    new = replace(new, options={k: v for k, v in new.options.items() if k not in BEHAVIOUR})
    if (old.type, old.options) == (new.type, new.options):
        return
    if not FIELD_TYPES[new.type].keeps_values(old, new):
        raise Problem(
            409,
            "unsafe_change",
            "A field's type can only change where no value is lost.",
            detail=f"{where} can't change from {old.type} {old.options} to {new.type}"
            f" {new.options}.",
        )


async def _write_fields(
    conn: AsyncConnection, where: str, table_id: uuid.UUID, declared: Table, types: dict[str, Any]
) -> None:
    existing = {
        row.name: row
        for row in await conn.execute(
            text(
                "SELECT f.sys_id, f.name, t.name AS type, f.options FROM sys.field f"
                ' JOIN sys.field_type t ON t.sys_id = f.type WHERE f."table" = :table'
            ),
            {"table": table_id},
        )
    }
    for position, (name, new) in enumerate(declared.fields.items()):
        values = {
            "table": table_id,
            "name": name,
            "type": types[new.type],
            "label": _json(new.label),
            "help": _json(new.help),
            "options": _json(new.options),
            "required": new.required,
            "unique": new.unique,
            "default": _json(new.default),
            "personal": new.personal,
            "position": position,
        }
        old = existing.get(name)
        if old is None:
            await conn.execute(
                text(f"INSERT INTO sys.field ({_FIELD_COLUMNS}) VALUES ({_FIELD_PARAMS})"),  # noqa: S608
                values,
            )
            continue
        _refuse_loss(
            f"{where}.{name}",
            Field(old.type, {}, old.options or {}),
            new,
        )
        await conn.execute(
            text(
                f"UPDATE sys.field SET ({_FIELD_COLUMNS}, deleted) = ({_FIELD_PARAMS}, NULL)"  # noqa: S608
                " WHERE sys_id = :id"
            ),
            {**values, "id": old.sys_id},
        )
    retired = [row.sys_id for name, row in existing.items() if name not in declared.fields]
    await conn.execute(
        text("UPDATE sys.field SET deleted = now() WHERE deleted IS NULL AND sys_id = ANY(:ids)"),
        {"ids": retired},
    )


async def write_declarations(
    conn: AsyncConnection, schema: str, tables: list[Table], *, retire_others: bool = True
) -> None:
    """Writes a module's tables into the metadata records; what it no longer declares is retired.

    Retired tables and fields keep their records, columns and data, hidden and read-only.
    """
    check(schema, tables)
    types = {
        name: row_id
        for name, row_id in await conn.execute(
            text("SELECT name, sys_id FROM sys.field_type WHERE deleted IS NULL")
        )
    }
    for declared in tables:
        table_id = await _write_table(conn, schema, declared)
        await _write_fields(conn, f"{schema}.{declared.name}", table_id, declared, types)
    if not retire_others:  # one table changed on its own (admins' tables)
        return
    await conn.execute(
        text(
            'UPDATE sys."table" SET deleted = now()'
            ' WHERE deleted IS NULL AND "schema" = :schema AND NOT name = ANY(:names)'
        ),
        {"schema": schema, "names": [t.name for t in tables]},
    )


async def declare(conn: AsyncConnection, schema: str, tables: list[Table]) -> list[str]:
    """Writes the declarations and builds the database from all records, in one transaction.

    Runs as the owner. Returns what was changed in the database, in plain words.
    """
    await write_declarations(conn, schema, tables)
    return await apply(conn, await load_catalog(conn))


async def build_metadata(conn: AsyncConnection, core_tables: list[Table]) -> list[str]:
    """The core's tables: the metadata tables built from their declarations first, then all of
    them (with `core_tables`, the ones upper layers declare) described by records and built."""
    done = await apply(conn, to_catalog(CORE, METADATA_TABLES))
    await sync_field_types(conn)
    return done + await declare(conn, CORE, [*METADATA_TABLES, *core_tables])
