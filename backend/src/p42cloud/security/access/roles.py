"""Roles as modules declare them, and writing them into the role records.

    Role("task.editor", "Task editor", grants=(
        Grant("task.task", ("read", "create", "write")),
        Grant("task.task", ("delete",),
              condition={"field": "opened_by", "op": "eq", "value": "@me"}),
    ), includes=("task.reader",))

Writing them is like writing table declarations: checked first, then the records are brought to
what's declared, and what a module no longer declares is retired. Custom roles, built in the app,
are never touched.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.conditions import Filter, Query, Situation, field_of
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.models import OPERATIONS


@dataclass(frozen=True)
class Grant:
    table: str  # schema.name
    operations: tuple[str, ...]
    condition: Any = None  # the filter language; None: every record
    fields: tuple[str, ...] | None = None  # None: every field


@dataclass(frozen=True)
class Role:
    name: str  # <module>.<name>
    label: str
    grants: tuple[Grant, ...] = ()
    includes: tuple[str, ...] = ()
    powerful: bool = False
    description: str | None = None


def _grant_problems(catalog: Catalog, where: str, grant: Grant) -> list[FieldError]:
    table = catalog.table(grant.table)
    if table is None or table.retired:
        return [FieldError(where, "unknown_table", f"No table {grant.table!r}.")]
    found = [
        FieldError(where, "bad_operation", f"No operation {op!r}.")
        for op in grant.operations
        if op not in OPERATIONS
    ]
    found += [
        FieldError(where, "unknown_field", f"{grant.table} has no field {name!r}.")
        for name in grant.fields or ()
        if field_of(table, name) is None
    ]
    if grant.condition is not None:
        try:
            situation = Situation(uuid.UUID(int=0))
            Filter(catalog, situation, Query()).sql(table, "t", grant.condition)
        except Problem as problem:
            found += [FieldError(where, e.code, e.message) for e in problem.errors]
    return found


def _cycle(roles: list[Role]) -> str | None:
    """A role that includes itself, through others; None if there's no such loop."""
    includes = {r.name: r.includes for r in roles}

    def reaches(start: str, name: str, seen: frozenset[str]) -> bool:
        return any(
            i == start or (i not in seen and reaches(start, i, seen | {i}))
            for i in includes.get(name, ())
        )

    return next((r.name for r in roles if reaches(r.name, r.name, frozenset())), None)


def check(catalog: Catalog, module: str, roles: list[Role]) -> None:
    found: list[FieldError] = []
    for role in roles:
        if not role.name.startswith(f"{module}."):
            found.append(FieldError(role.name, "bad_name", f"Must start with {module}."))
        for number, grant in enumerate(role.grants):
            found += _grant_problems(catalog, f"{role.name}.grants[{number}]", grant)
    if (looping := _cycle(roles)) is not None:
        found.append(FieldError(looping, "cycle", "The role includes itself."))
    if found:
        raise Problem(422, "invalid_roles", "The roles can't be used.", errors=found)


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


async def _id_of(conn: AsyncConnection, name: str) -> uuid.UUID:
    found = await conn.scalar(text("SELECT sys_id FROM sys.role WHERE name = :n"), {"n": name})
    if not isinstance(found, uuid.UUID):
        raise Problem(422, "invalid_roles", "A role includes an unknown role.", detail=name)
    return found


async def _write_role(conn: AsyncConnection, module: str, role: Role) -> uuid.UUID:
    values = {
        "name": role.name,
        "label": _json({"en": role.label}),
        "description": _json({"en": role.description} if role.description else None),
        "module": module,
        "powerful": role.powerful,
    }
    found = await conn.scalar(
        text("SELECT sys_id FROM sys.role WHERE name = :name"), {"name": role.name}
    )
    if found is None:
        found = await conn.scalar(
            text(
                "INSERT INTO sys.role (name, label, description, module, powerful, custom,"
                " switched_off) VALUES (:name, CAST(:label AS jsonb), CAST(:description AS jsonb),"
                " :module, :powerful, false, false) RETURNING sys_id"
            ),
            values,
        )
    else:
        await conn.execute(
            text(
                "UPDATE sys.role SET label = CAST(:label AS jsonb),"
                " description = CAST(:description AS jsonb), module = :module,"
                " powerful = :powerful, custom = false, deleted = NULL WHERE sys_id = :id"
            ),
            {**values, "id": found},
        )
    if not isinstance(found, uuid.UUID):
        raise RuntimeError(f"no ID for the role {role.name}")
    return found


async def _write_grants(conn: AsyncConnection, role_id: uuid.UUID, role: Role) -> None:
    tables = {
        f"{schema}.{name}": row_id
        for row_id, schema, name in await conn.execute(
            text('SELECT sys_id, "schema", name FROM sys."table"')
        )
    }
    wanted = [
        (tables[g.table], op, g.condition, list(g.fields) if g.fields is not None else None)
        for g in role.grants
        for op in g.operations
    ]
    existing = (
        await conn.execute(
            text(
                'SELECT sys_id, "table", operation, condition, fields, deleted IS NOT NULL'
                ' AS gone FROM sys."grant" WHERE role = :role'
            ),
            {"role": role_id},
        )
    ).all()
    kept: set[uuid.UUID] = set()
    for table_id, operation, condition, fields in wanted:
        match = next(
            (
                row
                for row in existing
                if (row.table, row.operation, row.condition, row.fields)
                == (table_id, operation, condition, fields)
                and row.sys_id not in kept
            ),
            None,
        )
        if match is not None:
            kept.add(match.sys_id)
            if match.gone:
                await conn.execute(
                    text('UPDATE sys."grant" SET deleted = NULL WHERE sys_id = :id'),
                    {"id": match.sys_id},
                )
            continue
        await conn.execute(
            text(
                'INSERT INTO sys."grant" (role, "table", operation, condition, fields)'
                " VALUES (:role, :table, :operation, CAST(:condition AS jsonb),"
                " CAST(:fields AS jsonb))"
            ),
            {
                "role": role_id,
                "table": table_id,
                "operation": operation,
                "condition": _json(condition),
                "fields": _json(fields),
            },
        )
    stale = [row.sys_id for row in existing if row.sys_id not in kept and not row.gone]
    await conn.execute(
        text('UPDATE sys."grant" SET deleted = now() WHERE sys_id = ANY(:ids)'), {"ids": stale}
    )


async def _write_includes(conn: AsyncConnection, role_id: uuid.UUID, role: Role) -> None:
    wanted = {await _id_of(conn, name) for name in role.includes}
    existing = {
        row.includes: row
        for row in await conn.execute(
            text(
                "SELECT sys_id, includes, deleted IS NOT NULL AS gone FROM sys.role_include"
                " WHERE role = :role"
            ),
            {"role": role_id},
        )
    }
    for included in wanted - existing.keys():
        await conn.execute(
            text("INSERT INTO sys.role_include (role, includes) VALUES (:role, :includes)"),
            {"role": role_id, "includes": included},
        )
    for included, row in existing.items():
        should = "NULL" if included in wanted else "now()"
        if row.gone == (included in wanted):
            await conn.execute(
                text(f"UPDATE sys.role_include SET deleted = {should} WHERE sys_id = :id"),  # noqa: S608
                {"id": row.sys_id},
            )


async def write_roles(
    conn: AsyncConnection, catalog: Catalog, module: str, roles: list[Role]
) -> None:
    """Brings a module's role records to its declaration, in the caller's transaction (owner)."""
    check(catalog, module, roles)
    ids = {role.name: await _write_role(conn, module, role) for role in roles}
    for role in roles:
        await _write_grants(conn, ids[role.name], role)
    for role in roles:
        await _write_includes(conn, ids[role.name], role)
    await conn.execute(
        text(
            "UPDATE sys.role SET deleted = now() WHERE module = :module AND NOT custom"
            " AND deleted IS NULL AND NOT name = ANY(:names)"
        ),
        {"module": module, "names": list(ids)},
    )
