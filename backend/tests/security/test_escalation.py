"""No escalation: an access administrator manages access, but can't give themselves more.

Root is an administrator; Ace only an access administrator; Joe has no roles of his own.
"""

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from tests.security.helpers import acting, change, role_id


@dataclass
class Team:
    engine: AsyncEngine
    root: uuid.UUID
    ace: uuid.UUID
    joe: uuid.UUID
    access_team: uuid.UUID
    helpers: uuid.UUID


@pytest.fixture(scope="module")
async def team(upgraded: Settings) -> AsyncGenerator[Team]:
    engine = create_engine(upgraded.database_url)
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        root, ace, joe = [
            (await records.create("sys.person", {"name": f"{n} {tag}"})).sys_id
            for n in ("Root", "Ace", "Joe")
        ]
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": root})
        access_team = (await records.create("sys.group", {"name": f"Access {tag}"})).sys_id
        await records.create(
            "sys.group_role",
            {"group": access_team, "role": await role_id(records, "sys.access_admin")},
        )
        await records.create("sys.membership", {"group": access_team, "person": ace})
        helpers = (await records.create("sys.group", {"name": f"Helpers {tag}"})).sys_id
    yield Team(engine, root, ace, joe, access_team, helpers)
    await engine.dispose()


async def test_an_access_admin_can_not_join_the_administrators(team: Team) -> None:
    with pytest.raises(Problem) as refused:
        async with acting(team.engine, team.ace) as records:
            await records.create("sys.membership", {"group": ADMINISTRATORS, "person": team.ace})
    assert refused.value.code == "admins_only"


async def test_an_access_admin_can_not_hand_out_powerful_roles(team: Team) -> None:
    with pytest.raises(Problem) as refused:
        async with acting(team.engine, team.ace) as records:
            await records.create(
                "sys.group_role",
                {"group": team.access_team, "role": await role_id(records, "sys.admin")},
            )
    assert refused.value.code == "admins_only"


async def test_an_access_admin_can_not_take_powers_away_either(team: Team) -> None:
    async with acting(team.engine, team.ace) as records:
        page = await records.list(
            "sys.group_role", where={"field": "group", "op": "eq", "value": str(ADMINISTRATORS)}
        )
        held = page.records[0]
    with pytest.raises(Problem) as refused:
        async with acting(team.engine, team.ace) as records:
            await records.delete("sys.group_role", held.sys_id, held.etag)
    assert refused.value.code == "admins_only"


async def test_ordinary_roles_and_members_are_the_access_admins_job(team: Team) -> None:
    async with acting(team.engine, team.ace) as records:
        await records.create(
            "sys.group_role", {"group": team.helpers, "role": await role_id(records, "sys.user")}
        )
        joined = await records.create("sys.membership", {"group": team.helpers, "person": team.joe})
    assert joined.values["from_rule"] is False


async def test_roles_of_modules_are_read_only(team: Team) -> None:
    with pytest.raises(Problem) as refused:
        async with acting(team.engine, team.ace) as records:
            await change(
                records, "sys.role", await role_id(records, "sys.user"), {"label": "Anyone"}
            )
    assert refused.value.code == "declared_role"


async def test_custom_roles_are_made_of_what_modules_grant(team: Team) -> None:
    async with acting(team.engine, team.ace) as records:
        made = await records.create(
            "sys.role",
            {"name": f"custom.viewer.{uuid.uuid4().hex[:6]}", "label": "Viewer", "module": "x"},
        )
        person_table = (
            await records.list("sys.table", where={"field": "name", "op": "eq", "value": "person"})
        ).records[0]
        # Reading people is something an ordinary module role (sys.user) grants: still ordinary.
        await records.create(
            "sys.grant",
            {"role": made.sys_id, "table": person_table.sys_id, "operation": "read"},
        )
        ordinary = await records.read("sys.role", made.sys_id)
        with pytest.raises(Problem) as undeclared:
            field_table = (
                await records.list(
                    "sys.table", where={"field": "name", "op": "eq", "value": "field"}
                )
            ).records[0]
            await records.create(
                "sys.grant",
                {"role": made.sys_id, "table": field_table.sys_id, "operation": "write"},
            )
    assert (made.values["custom"], made.values["module"]) == (True, "custom")
    assert ordinary.values["powerful"] is False
    assert undeclared.value.code == "not_declared"


async def test_copying_a_power_makes_a_custom_role_powerful(team: Team) -> None:
    async with acting(team.engine, team.ace) as records:
        made = await records.create(
            "sys.role", {"name": f"custom.hr.{uuid.uuid4().hex[:6]}", "label": "HR"}
        )
        people = (
            await records.list("sys.table", where={"field": "name", "op": "eq", "value": "person"})
        ).records[0]
        # Changing people is granted only by sys.admin, a powerful role.
        await records.create(
            "sys.grant", {"role": made.sys_id, "table": people.sys_id, "operation": "write"}
        )
        powerful = await records.read("sys.role", made.sys_id)
    with pytest.raises(Problem) as refused:
        async with acting(team.engine, team.ace) as records:
            await records.create("sys.group_role", {"group": team.helpers, "role": made.sys_id})
    async with acting(team.engine, team.root) as records:
        given = await records.create("sys.group_role", {"group": team.helpers, "role": made.sys_id})
    assert powerful.values["powerful"] is True
    assert refused.value.code == "admins_only"
    assert given.values["role"] == made.sys_id


async def test_a_held_role_can_not_be_made_powerful_by_an_access_admin(team: Team) -> None:
    async with acting(team.engine, team.ace) as records:
        made = await records.create(
            "sys.role", {"name": f"custom.desk.{uuid.uuid4().hex[:6]}", "label": "Desk"}
        )
        await records.create("sys.group_role", {"group": team.helpers, "role": made.sys_id})
        people = (
            await records.list("sys.table", where={"field": "name", "op": "eq", "value": "person"})
        ).records[0]
    with pytest.raises(Problem) as refused:
        async with acting(team.engine, team.ace) as records:
            await records.create(
                "sys.grant", {"role": made.sys_id, "table": people.sys_id, "operation": "write"}
            )
    async with acting(team.engine, team.ace) as records:
        still = await records.read("sys.role", made.sys_id)
        grants = await records.list(
            "sys.grant", where={"field": "role", "op": "eq", "value": str(made.sys_id)}
        )
    assert refused.value.code == "admins_only"
    assert still.values["powerful"] is False
    assert grants.records == []  # all or nothing: the grant is gone too


async def test_including_a_powerful_role_is_powerful(team: Team) -> None:
    async with acting(team.engine, team.ace) as records:
        made = await records.create(
            "sys.role", {"name": f"custom.boss.{uuid.uuid4().hex[:6]}", "label": "Boss"}
        )
        await records.create(
            "sys.role_include",
            {"role": made.sys_id, "includes": await role_id(records, "sys.admin")},
        )
        found = await records.read("sys.role", made.sys_id)
        with pytest.raises(Problem) as refused:
            await change(records, "sys.role", made.sys_id, {"powerful": False})
    assert found.values["powerful"] is True
    assert refused.value.code == "system_field"
