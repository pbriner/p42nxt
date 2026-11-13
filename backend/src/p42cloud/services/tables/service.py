"""Tables administrators build in the app: in the schema `u`, like any declared table.

Making or changing one writes its records and changes the database in one transaction, as the
owner, with the engine's rules (no lost values; removed fields retired). Only administrators may,
with the second factor given in the last 15 minutes. Each table gets a reader and an editor role
(`u.<table>.reader`, `u.<table>.editor`): given to groups like any role, and the grants custom
roles may copy.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import Catalog, load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.metadata import write_declarations
from p42cloud.data.schema import apply
from p42cloud.data.types import FIELD_TYPES, Field
from p42cloud.kernel.context import current
from p42cloud.kernel.db import ensure_module_role, owner_transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.guards import RECENT, holds_admin
from p42cloud.security.access.roles import Grant, Role, write_roles

ADMIN_SCHEMA = "u"
ALL = ("read", "create", "write", "delete", "restore")
_FIELD_KEYS = frozenset({"type", "label", "options", "required", "unique", "default", "help"})


def _invalid(errors: list[FieldError]) -> Problem:
    return Problem(422, "invalid_declaration", "The table can't be built.", errors=errors)


def table_from(body: dict[str, Any]) -> Table:
    """A table declaration from the API's JSON: {name, label, label_plural, display, fields}."""
    fields: dict[str, Field] = {}
    errors: list[FieldError] = []
    given = body.get("fields")
    if not isinstance(given, dict):
        raise _invalid([FieldError("fields", "required", "The table needs its fields.")])
    for name, spec in cast(dict[str, Any], given).items():
        if not isinstance(spec, dict) or set(cast(dict[str, Any], spec)) - _FIELD_KEYS:
            errors.append(FieldError(name, "bad_field", f"A field is {sorted(_FIELD_KEYS)}."))
            continue
        field = cast(dict[str, Any], spec)
        if field.get("type") not in FIELD_TYPES:
            errors.append(FieldError(name, "unknown_type", f"No field type {field.get('type')!r}."))
            continue
        help_text = field.get("help")
        fields[name] = Field(
            type=field["type"],
            label={"en": str(field.get("label") or name)},
            options=dict(field.get("options") or {}),
            required=bool(field.get("required", False)),
            unique=bool(field.get("unique", False)),
            default=field.get("default"),
            help={"en": str(help_text)} if help_text else None,
        )
    if errors:
        raise _invalid(errors)
    return Table(
        name=str(body.get("name", "")),
        label=str(body.get("label") or body.get("name", "")),
        fields=fields,
        label_plural=body.get("label_plural"),
        display=body.get("display"),
    )


async def _administrators_only(conn: AsyncConnection) -> None:
    context = current()
    if context.access.unrestricted:
        return
    if context.verified_at is None or datetime.now(UTC) - context.verified_at > RECENT:
        raise Problem(403, "verify_again", "Enter a code from your authenticator app again first.")
    if not await holds_admin(conn, context.access.groups):
        raise Problem(403, "admins_only", "Only administrators build tables.")


def _roles(catalog: Catalog) -> list[Role]:
    roles: list[Role] = []
    for table in catalog.tables.values():
        if table.schema != ADMIN_SCHEMA or table.retired:
            continue
        label = str(table.label.get("en", table.name))
        roles += [
            Role(f"u.{table.name}.reader", f"{label}: read", (Grant(table.key, ("read",)),)),
            Role(f"u.{table.name}.editor", f"{label}: change", (Grant(table.key, ALL),)),
        ]
    return roles


async def save_table(owner: AsyncEngine, table: Table) -> list[str]:
    """Makes or changes one of the admins' tables; returns what changed in the database."""
    async with owner_transaction(owner) as conn:
        await _administrators_only(conn)
        await ensure_module_role(conn, ADMIN_SCHEMA)
        await write_declarations(conn, ADMIN_SCHEMA, [table], retire_others=False)
        catalog = await load_catalog(conn)
        done = await apply(conn, catalog)
        await write_roles(conn, catalog, ADMIN_SCHEMA, _roles(catalog))
    return done


async def retire_table(owner: AsyncEngine, name: str) -> None:
    """Retires one of the admins' tables: hidden and read-only, its data kept."""
    async with owner_transaction(owner) as conn:
        await _administrators_only(conn)
        retired = await conn.execute(
            text(
                'UPDATE sys."table" SET deleted = now() WHERE "schema" = :s AND name = :n'
                " AND deleted IS NULL"
            ),
            {"s": ADMIN_SCHEMA, "n": name},
        )
        if retired.rowcount == 0:
            raise Problem(404, "no_table", "There's no such table.", detail=f"u.{name}")
        await write_roles(
            conn, await load_catalog(conn), ADMIN_SCHEMA, _roles(await load_catalog(conn))
        )
