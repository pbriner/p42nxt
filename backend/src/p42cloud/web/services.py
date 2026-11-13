"""What the routes share in one process: connections, sign-in, providers, the catalog cache."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data import settings
from p42cloud.data.catalog import MetadataCache
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.providers.service import Providers
from p42cloud.security.sign_in.sessions import SignIn
from p42cloud.security.tokens.service import TokenUse, use_token

if TYPE_CHECKING:
    from p42cloud.web.caller import Rates


@dataclass
class Services:
    settings: Settings
    engine: AsyncEngine
    # The owner's connection: for changes to the database's tables (admins' tables) only.
    owner: AsyncEngine
    sealer: Sealer
    sign_in: SignIn
    providers: Providers | None  # needs the installation's address
    cache: MetadataCache
    rates: Rates
    http: httpx.AsyncClient

    async def time_zone(self) -> str:
        """The installation's time zone (a setting)."""
        async with transaction(self.engine, "sys") as conn:
            return str(await settings.value(conn, "sys.time_zone"))

    async def tokens(self, token: str, address: str | None) -> TokenUse:
        return await use_token(self.engine, token, address)

    @asynccontextmanager
    async def records(self, context: Context) -> AsyncGenerator[Records]:
        """One request's records: its context, the cached catalog, one transaction."""
        with working_as(context):
            catalog = await self.cache.catalog()
            async with transaction(self.engine, "sys") as conn:
                yield Records(conn, catalog)
