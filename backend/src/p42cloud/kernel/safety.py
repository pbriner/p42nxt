"""Safety checks the app runs before it starts: it refuses a database that isn't set up safely.

Checked as the app role, from the database's own catalog, so a right granted by hand later is
found too.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.kernel.db import APP_ROLE, MODULE_ROLE_PREFIX, OWNER_ROLE

MIN_SERVER_VERSION = 180000  # PostgreSQL 18: uuidv7(), virtual columns

_POWERS = text(
    "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls,"
    " rolinherit FROM pg_roles WHERE rolname IN (:app, :owner)"
)
_OWNED = text(
    "SELECT (SELECT count(*) FROM pg_class WHERE relowner = r.oid)"
    " + (SELECT count(*) FROM pg_namespace WHERE nspowner = r.oid)"
    " + (SELECT count(*) FROM pg_proc WHERE proowner = r.oid)"
    " FROM pg_roles r WHERE r.rolname = current_user"
)
_CREATE_IN = text(
    "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%'"
    " AND nspname <> 'information_schema'"
    " AND has_schema_privilege(current_user, nspname, 'CREATE') ORDER BY nspname"
)
_MEMBER_OF = text(
    "SELECT g.rolname, m.inherit_option FROM pg_auth_members m"
    " JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member"
    " WHERE u.rolname = current_user ORDER BY g.rolname"
)


# Records are never deleted: no role the app works as may delete rows or empty a table.
_DELETE_RIGHTS = text(
    "SELECT r.rolname, n.nspname || '.' || c.relname FROM pg_class c"
    " JOIN pg_namespace n ON n.oid = c.relnamespace CROSS JOIN pg_roles r"
    " WHERE c.relkind IN ('r', 'p') AND n.nspname NOT LIKE 'pg\\_%'"
    " AND n.nspname <> 'information_schema'"
    " AND (r.rolname = :app OR r.rolname LIKE :modules)"
    " AND (has_table_privilege(r.oid, c.oid, 'DELETE')"
    " OR has_table_privilege(r.oid, c.oid, 'TRUNCATE'))"
    " ORDER BY 1, 2"
)


async def problems(engine: AsyncEngine) -> list[str]:
    """Everything that makes the database unsafe for the app, in plain words; empty if safe."""
    async with engine.connect() as conn:
        return [
            *await _server_and_user(conn),
            *await _powers(conn),
            *await _ownership_and_creation(conn),
            *await _memberships(conn),
            *await _delete_rights(conn),
        ]


async def _server_and_user(conn: AsyncConnection) -> list[str]:
    found: list[str] = []
    version = await conn.scalar(text("SELECT current_setting('server_version_num')::int"))
    if version is None or version < MIN_SERVER_VERSION:
        found.append("PostgreSQL 18 or newer is needed.")
    user = await conn.scalar(text("SELECT current_user"))
    if user != APP_ROLE:
        found.append(f"The app must connect as {APP_ROLE}, not {user}.")
    return found


async def _powers(conn: AsyncConnection) -> list[str]:
    found: list[str] = []
    rows = (await conn.execute(_POWERS, {"app": APP_ROLE, "owner": OWNER_ROLE})).all()
    for name, superuser, createrole, createdb, replication, bypassrls, inherit in rows:
        powers = [
            label
            for label, has in (
                ("superuser", superuser),
                ("create roles", createrole),
                ("create databases", createdb),
                ("replication", replication),
                ("bypass row security", bypassrls),
            )
            if has
        ]
        if powers:
            found.append(f"The role {name} may {', '.join(powers)}.")
        if name == APP_ROLE and inherit:
            found.append(f"The role {APP_ROLE} inherits its roles; it must switch to them.")
    return found


async def _ownership_and_creation(conn: AsyncConnection) -> list[str]:
    found: list[str] = []
    if await conn.scalar(_OWNED):
        found.append(f"The role {APP_ROLE} owns database objects; it must own nothing.")
    if await conn.scalar(
        text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")
    ):
        found.append(f"The role {APP_ROLE} may create schemas.")
    schemas = list((await conn.execute(_CREATE_IN)).scalars())
    if schemas:
        found.append(f"The role {APP_ROLE} may create objects in: {', '.join(schemas)}.")
    return found


async def _memberships(conn: AsyncConnection) -> list[str]:
    found: list[str] = []
    for group, inherits in (await conn.execute(_MEMBER_OF)).all():
        if not group.startswith(MODULE_ROLE_PREFIX):
            found.append(f"The role {APP_ROLE} is a member of {group}, not a module role.")
        elif inherits:
            found.append(f"The role {APP_ROLE} inherits the rights of {group}.")
    return found


async def _delete_rights(conn: AsyncConnection) -> list[str]:
    rows = await conn.execute(
        _DELETE_RIGHTS, {"app": APP_ROLE, "modules": MODULE_ROLE_PREFIX.replace("_", "\\_") + "%"}
    )
    return [f"The role {role} may delete rows of {table}." for role, table in rows.all()]
