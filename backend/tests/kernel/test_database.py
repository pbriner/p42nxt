"""Setting up the database, the safety checks, and transactions as a module's role."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import module_role, transaction
from p42cloud.kernel.safety import problems
from p42cloud.kernel.setup import set_up_database
from tests.conftest import ADMIN_URL


async def test_a_fresh_installation_is_safe(app_engine: AsyncEngine) -> None:
    assert await problems(app_engine) == []


async def test_setting_up_again_changes_nothing(
    installed: Settings, app_engine: AsyncEngine
) -> None:
    database = installed.database_url.rsplit("/", 1)[1]
    await set_up_database(ADMIN_URL.rsplit("/", 1)[0] + f"/{database}", installed)
    assert await problems(app_engine) == []


async def test_a_right_granted_by_hand_is_found(
    app_engine: AsyncEngine, admin_conn: AsyncEngine
) -> None:
    async with admin_conn.connect() as conn:
        await conn.execute(text("GRANT CREATE ON SCHEMA sys TO p42_app"))
    try:
        assert await problems(app_engine) == ["The role p42_app may create objects in: sys."]
    finally:
        async with admin_conn.connect() as conn:
            await conn.execute(text("REVOKE CREATE ON SCHEMA sys FROM p42_app"))


async def test_inheriting_a_module_role_is_found(
    app_engine: AsyncEngine, admin_conn: AsyncEngine
) -> None:
    async with admin_conn.connect() as conn:
        await conn.execute(text("GRANT p42_mod_sys TO p42_app WITH INHERIT TRUE"))
    try:
        assert await problems(app_engine) == [
            "The role p42_app inherits the rights of p42_mod_sys."
        ]
    finally:
        async with admin_conn.connect() as conn:
            await conn.execute(text("GRANT p42_mod_sys TO p42_app WITH INHERIT FALSE"))


async def test_work_runs_as_the_module_role_with_the_context(app_engine: AsyncEngine) -> None:
    context = Context(channel="test")
    with working_as(context):
        async with transaction(app_engine, "sys") as conn:
            role = await conn.scalar(text("SELECT current_user"))
            request_id = await conn.scalar(text("SELECT current_setting('p42.request_id')"))
            channel = await conn.scalar(text("SELECT current_setting('p42.channel')"))
    assert (role, request_id, channel) == ("p42_mod_sys", str(context.request_id), "test")


async def test_the_app_alone_can_not_create_anything(app_engine: AsyncEngine) -> None:
    with working_as(Context(channel="test")), pytest.raises(DBAPIError):
        async with transaction(app_engine, "sys") as conn:
            await conn.execute(text("CREATE TABLE sys.intruder (id int)"))


def test_module_roles_only_from_module_ids() -> None:
    assert module_role("task") == "p42_mod_task"
    with pytest.raises(ValueError, match="not a module ID"):
        module_role('x"; DROP ROLE p42_owner; --')
