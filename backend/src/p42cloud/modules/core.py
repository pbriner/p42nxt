"""The core module (`sys`), assembled from the features' parts: its tables, roles and first records.

Until the module system exists, this is where the core is put together and installed.
"""

from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.access import SHARE
from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.upgrade import upgrade
from p42cloud.kernel.db import owner_transaction
from p42cloud.security.access import hooks as access_hooks
from p42cloud.security.access import manifest as access
from p42cloud.security.access import models as access_models
from p42cloud.security.access.roles import Role, write_roles
from p42cloud.security.people import models as people
from p42cloud.security.sign_in import hooks as sign_in_hooks
from p42cloud.security.sign_in import models as sign_in

CORE = "sys"
TABLES: list[Table] = [*people.TABLES, *access_models.TABLES, SHARE, *sign_in.TABLES]
ROLES: list[Role] = access.ROLES


def compose() -> None:
    """Connects the core's features to each other (their hooks); every process does it once."""
    access_hooks.register()
    sign_in_hooks.register()


async def install_core(engine: AsyncEngine) -> list[str]:
    """Brings the database and the core's records to this version (as the owner, in a context)."""
    compose()
    done = await upgrade(engine, TABLES)
    async with owner_transaction(engine) as conn:
        catalog = await load_catalog(conn)
        await write_roles(conn, catalog, CORE, ROLES)
        await access.seed(conn, catalog)
    return done
