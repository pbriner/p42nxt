"""What access adds to the core: its roles, and the groups a new installation starts with."""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.engine_steps import DATABASE_PERSON
from p42cloud.kernel.context import SYSTEM_PERSON
from p42cloud.security.access.groups import recalculate_group
from p42cloud.security.access.models import OPERATIONS
from p42cloud.security.access.roles import Grant, Role

ALL = tuple(OPERATIONS)
PEOPLE = "sys.person"
ACCESS_TABLES = (
    "sys.group",
    "sys.membership",
    "sys.role",
    "sys.role_include",
    "sys.grant",
    "sys.group_role",
)
METADATA = ("sys.table", "sys.field", "sys.field_type")
SHARES = "sys.share"
MINE = {"field": "owner", "op": "eq", "value": "@me"}

ROLES = [
    Role(
        "sys.admin",
        "Administrator",
        grants=(
            Grant(PEOPLE, ALL),
            *(Grant(t, ("read",)) for t in (*ACCESS_TABLES, *METADATA)),
        ),
        powerful=True,
        description="Runs the platform: people, settings, modules.",
    ),
    Role(
        "sys.access_admin",
        "Access administrator",
        grants=(
            Grant(PEOPLE, ("read",)),
            *(Grant(t, ALL) for t in ACCESS_TABLES),
            # Custom roles are built from tables and fields.
            *(Grant(t, ("read",)) for t in METADATA),
        ),
        powerful=True,
        description="Manages groups, their members and roles, and custom roles.",
    ),
    Role(
        "sys.user",
        "User",
        grants=(
            Grant(PEOPLE, ("read",), fields=("name", "kind", "active")),
            Grant("sys.group", ("read",), fields=("name", "description", "assignable")),
            Grant(SHARES, ALL, condition=MINE),
            Grant(SHARES, ("read",), condition={"field": "person", "op": "eq", "value": "@me"}),
        ),
        description="Everyone: sees who's who and shares their own records.",
    ),
]

# Fixed IDs, the same in every installation.
ADMINISTRATORS = uuid.UUID("00000000-0000-7000-8000-000000000101")
EVERYONE = uuid.UUID("00000000-0000-7000-8000-000000000102")
STARTING_GROUPS = (
    (
        ADMINISTRATORS,
        "Administrators",
        "Run the platform and manage access.",
        None,
        ("sys.admin", "sys.access_admin"),
    ),
    (
        EVERYONE,
        "Everyone",
        "Every active employee and external person.",
        {"field": "kind", "op": "in", "value": ["employee", "external"]},
        ("sys.user",),
    ),
)


async def seed(conn: AsyncConnection, catalog: Catalog) -> None:
    """The system's own people and, on a new installation, the starting groups.

    Each group is made once: later it may be renamed, or its roles moved to other groups.
    """
    for person, name in ((SYSTEM_PERSON, "System"), (DATABASE_PERSON, "Database")):
        await conn.execute(
            text(
                "INSERT INTO sys.person (sys_id, name, kind, active)"
                " VALUES (:id, :name, 'service', true)"
                " ON CONFLICT (sys_id) DO NOTHING"
            ),
            {"id": person, "name": name},
        )
    for group, name, description, rule, roles in STARTING_GROUPS:
        made = await conn.scalar(
            text(
                'INSERT INTO sys."group" (sys_id, name, description, assignable, rule)'
                " VALUES (:id, :name, :description, false, CAST(:rule AS jsonb))"
                " ON CONFLICT (sys_id) DO NOTHING RETURNING sys_id"
            ),
            {
                "id": group,
                "name": name,
                "description": description,
                "rule": json.dumps(rule) if rule else None,
            },
        )
        if made is None:
            continue
        await conn.execute(
            text(
                'INSERT INTO sys.group_role ("group", role)'
                " SELECT :group, sys_id FROM sys.role WHERE name = ANY(:roles)"
            ),
            {"group": group, "roles": list(roles)},
        )
        await recalculate_group(conn, catalog, group)
