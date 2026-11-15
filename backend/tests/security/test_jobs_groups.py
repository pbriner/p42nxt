"""A module's jobs' own group: no people join it, no other roles; and a new module's roles are
announced to administrators."""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from tests.security.helpers import acting, change, role_id


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def _jobs_group(upgraded: Settings, name: str) -> uuid.UUID:
    """A group as the platform makes one for a module's jobs."""
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="upgrade")):
        async with owner_transaction(owner) as conn:
            made = await conn.scalar(
                text(
                    'INSERT INTO sys."group" (name, assignable, jobs_of)'
                    " VALUES (:n, false, 'zz') RETURNING sys_id"
                ),
                {"n": name},
            )
    await owner.dispose()
    assert isinstance(made, uuid.UUID)
    return made


async def test_people_and_roles_stay_out_of_a_modules_jobs_group(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    tag = uuid.uuid4().hex[:6]
    group = await _jobs_group(upgraded, f"zz jobs {tag}")
    async with acting(engine, None) as records:
        person = (await records.create("sys.person", {"name": f"Joiner {tag}"})).sys_id
        with pytest.raises(Problem) as refused:
            await records.create("sys.membership", {"group": group, "person": person})
        assert refused.value.code == "jobs_group"
        with pytest.raises(Problem) as refused:
            await records.create(
                "sys.group_role", {"group": group, "role": await role_id(records, "sys.user")}
            )
        assert refused.value.code == "jobs_group"
        # Nor can the marker be taken away (or put on another group).
        with pytest.raises(Problem) as refused:
            await change(records, "sys.group", group, {"jobs_of": None})
        assert refused.value.code == "jobs_group"


async def test_someone_who_joined_before_can_leave(engine: AsyncEngine, upgraded: Settings) -> None:
    tag = uuid.uuid4().hex[:6]
    group = await _jobs_group(upgraded, f"zz jobs {tag}")
    async with acting(engine, None) as records:
        person = (await records.create("sys.person", {"name": f"Leaver {tag}"})).sys_id
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="upgrade")):
        async with owner_transaction(owner) as conn:  # as it happened before the rule
            joined = await conn.scalar(
                text(
                    'INSERT INTO sys.membership ("group", person, from_rule)'
                    " VALUES (:g, :p, false) RETURNING sys_id"
                ),
                {"g": group, "p": person},
            )
    await owner.dispose()
    async with acting(engine, None) as records:
        found = await records.read("sys.membership", joined)
        await records.delete("sys.membership", joined, found.etag)
