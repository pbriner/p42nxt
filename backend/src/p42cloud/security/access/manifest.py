"""What access adds to the core: its roles, and the Administrators group made at setup."""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.engine_steps import DATABASE_PERSON
from p42cloud.kernel.context import SYSTEM_PERSON
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
        grants=(Grant(PEOPLE, ("read",)), *(Grant(t, ALL) for t in ACCESS_TABLES)),
        powerful=True,
        description="Manages groups, their members and roles, and custom roles.",
    ),
]

# Fixed IDs, the same in every installation.
ADMINISTRATORS = uuid.UUID("00000000-0000-7000-8000-000000000101")


async def seed(conn: AsyncConnection) -> None:
    """The system's own people and, on a new installation, the Administrators group.

    The group is made once: later it may be renamed, or its roles moved to other groups.
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
    made = await conn.scalar(
        text(
            'INSERT INTO sys."group" (sys_id, name, description, assignable)'
            " VALUES (:id, 'Administrators', 'Run the platform and manage access.', false)"
            " ON CONFLICT (sys_id) DO NOTHING RETURNING sys_id"
        ),
        {"id": ADMINISTRATORS},
    )
    if made is None:
        return
    await conn.execute(
        text(
            'INSERT INTO sys.group_role ("group", role)'
            " SELECT :group, sys_id FROM sys.role WHERE name IN ('sys.admin', 'sys.access_admin')"
        ),
        {"group": ADMINISTRATORS},
    )
