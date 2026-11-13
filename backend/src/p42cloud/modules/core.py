"""The core module (`sys`), assembled from the features' parts: its tables, roles and first records.

Until the module system exists, this is where the core is put together and installed.
"""

from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.upgrade import upgrade
from p42cloud.kernel.db import owner_transaction
from p42cloud.security.access import manifest as access
from p42cloud.security.access import models as access_models
from p42cloud.security.access.roles import Role, write_roles
from p42cloud.security.people import models as people

CORE = "sys"
TABLES: list[Table] = [*people.TABLES, *access_models.TABLES]
ROLES: list[Role] = access.ROLES


async def install_core(engine: AsyncEngine) -> list[str]:
    """Brings the database and the core's records to this version (as the owner, in a context)."""
    done = await upgrade(engine, TABLES)
    async with owner_transaction(engine) as conn:
        await write_roles(conn, await load_catalog(conn), CORE, ROLES)
        await access.seed(conn)
    return done
