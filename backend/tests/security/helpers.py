"""Acting as a person in tests: their access worked out from their groups, as a request would."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Record, Records
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.security.access.service import access_for


async def context_of(engine: AsyncEngine, person: uuid.UUID | None) -> Context:
    """A request by the person, with the access their groups give them (None: the system)."""
    if person is None:
        return Context(channel="ui", access=EVERYTHING)
    with working_as(Context(channel="ui", person_id=person)):
        async with transaction(engine, "sys") as conn:
            access = await access_for(conn, person)
    # As if they had just given their second factor (access changes need it to be recent).
    return Context(channel="ui", person_id=person, access=access, verified_at=datetime.now(UTC))


@asynccontextmanager
async def acting(engine: AsyncEngine, person: uuid.UUID | None) -> AsyncGenerator[Records]:
    """Records for one request by the person (None: the system); committed at the end."""
    with working_as(await context_of(engine, person)):
        async with transaction(engine, "sys") as conn:
            yield Records(conn, await load_catalog(conn))


async def role_id(records: Records, name: str) -> uuid.UUID:
    found = await records.conn.scalar(
        text("SELECT sys_id FROM sys.role WHERE name = :n"), {"n": name}
    )
    assert isinstance(found, uuid.UUID)
    return found


async def fresh(records: Records, key: str, sys_id: uuid.UUID) -> Record:
    return await records.read(key, sys_id)


async def change(records: Records, key: str, sys_id: uuid.UUID, values: dict[str, Any]) -> Record:
    """An update with the record's current version."""
    return await records.update(key, sys_id, values, (await records.read(key, sys_id)).etag)
