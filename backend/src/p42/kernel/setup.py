"""Setting up an installation's database: its roles and the core's schema.

Runs once, when an installation is created, with an administrator connection that is used for
nothing else and not kept. Safe to run again: it brings roles and rights back to what they
should be.
"""

import re

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from p42.kernel.config import Settings
from p42.kernel.db import APP_ROLE, OWNER_ROLE, module_role

CORE_MODULE = "sys"
_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")

# Roles get no powers beyond logging in; the app role doesn't even inherit its module roles,
# it has to switch to one explicitly.
_LIMITS = "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"


def _ident(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"not a plain database name: {name!r}")
    return f'"{name}"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _password(url: URL) -> str:
    if not url.password:
        raise ValueError(f"no password for the role {url.username!r}")
    return str(url.password)


async def _ensure_role(conn: AsyncConnection, name: str, options: str) -> None:
    exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :n"), {"n": name})
    verb = "ALTER" if exists else "CREATE"
    await conn.execute(text(f"{verb} ROLE {_ident(name)} {options}"))


async def set_up_database(admin_url: str, settings: Settings) -> None:
    owner, app = make_url(settings.owner_database_url), make_url(settings.database_url)
    if owner.username != OWNER_ROLE or app.username != APP_ROLE:
        raise ValueError(f"the roles must be named {OWNER_ROLE} and {APP_ROLE}")
    database = _ident(str(app.database))
    core_role = module_role(CORE_MODULE)

    # Roles and database rights, connected to the administrator's own database.
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await _ensure_role(
                conn, OWNER_ROLE, f"LOGIN {_LIMITS} PASSWORD {_literal(_password(owner))}"
            )
            await _ensure_role(
                conn, APP_ROLE, f"LOGIN NOINHERIT {_LIMITS} PASSWORD {_literal(_password(app))}"
            )
            await _ensure_role(conn, core_role, f"NOLOGIN NOINHERIT {_LIMITS}")
            await conn.execute(
                text(f"GRANT {_ident(core_role)} TO {APP_ROLE} WITH INHERIT FALSE, SET TRUE")
            )
            await conn.execute(text(f"ALTER DATABASE {database} OWNER TO {OWNER_ROLE}"))
            await conn.execute(text(f"REVOKE ALL ON DATABASE {database} FROM PUBLIC"))
            await conn.execute(text(f"GRANT CONNECT ON DATABASE {database} TO {APP_ROLE}"))
    finally:
        await engine.dispose()

    # The core's schema, in the installation's database.
    target = make_url(admin_url).set(database=app.database)
    engine = create_async_engine(target, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
            await conn.execute(
                text(f"CREATE SCHEMA IF NOT EXISTS {CORE_MODULE} AUTHORIZATION {OWNER_ROLE}")
            )
            await conn.execute(text(f"GRANT USAGE ON SCHEMA {CORE_MODULE} TO {_ident(core_role)}"))
    finally:
        await engine.dispose()
