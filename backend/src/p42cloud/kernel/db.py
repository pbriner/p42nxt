"""Database access: engines, and transactions that run as a module's role with the context set.

The app connects with a role that owns nothing and has no rights on tables of its own. Every
transaction switches to the role of the module doing the work, which may only write that
module's schema (docs/architecture.md, Runtime). The context (request ID, person, channel) is
handed to the database for the audit trigger.
"""

import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from p42cloud.kernel.context import current

OWNER_ROLE = "p42_owner"
APP_ROLE = "p42_app"
MODULE_ROLE_PREFIX = "p42_mod_"
_MODULE_ID = re.compile(r"^[a-z][a-z0-9]{1,15}$")


def module_role(module_id: str) -> str:
    """The database role a module's work runs as; it may only write the module's schema."""
    if not _MODULE_ID.match(module_id):
        raise ValueError(f"not a module ID: {module_id!r}")
    return f"{MODULE_ROLE_PREFIX}{module_id}"


async def run_script(conn: AsyncConnection, sql: str) -> None:
    """Several statements at once (the engine's steps), inside the connection's transaction.

    Only for SQL written in the code: the driver's plain protocol takes no parameters.
    """
    raw = await conn.get_raw_connection()
    await raw.driver_connection.execute(sql)  # pyright: ignore[reportOptionalMemberAccess, reportUnknownMemberType]


def create_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


async def _hand_over_context(conn: AsyncConnection) -> None:
    context = current()
    await conn.execute(
        text(
            "SELECT set_config('p42.request_id', :request_id, true),"
            " set_config('p42.person_id', :person_id, true),"
            " set_config('p42.channel', :channel, true)"
        ),
        {
            "request_id": str(context.request_id),
            "person_id": str(context.person_id),
            "channel": context.channel,
        },
    )


async def switch_role(conn: AsyncConnection, module_id: str) -> None:
    """Work as `module_id` from here to the end of the transaction (one module's part of it)."""
    role = module_role(module_id)  # checked, so it's safe to put into the statement
    await conn.execute(text(f'SET LOCAL ROLE "{role}"'))


@asynccontextmanager
async def transaction(engine: AsyncEngine, module_id: str) -> AsyncGenerator[AsyncConnection]:
    """One transaction as `module_id`'s role, with the current context handed to the database."""
    async with engine.begin() as conn:
        await switch_role(conn, module_id)
        await _hand_over_context(conn)
        yield conn


@asynccontextmanager
async def owner_transaction(engine: AsyncEngine) -> AsyncGenerator[AsyncConnection]:
    """One transaction as the owner (installing and upgrading only), with the context set."""
    async with engine.begin() as conn:
        user = await conn.scalar(text("SELECT current_user"))
        if user != OWNER_ROLE:
            raise RuntimeError(f"expected the owner role {OWNER_ROLE}, connected as {user}")
        await _hand_over_context(conn)
        yield conn
