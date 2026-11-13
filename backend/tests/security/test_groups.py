"""Groups with a rule: members follow the rule in the same transaction, and nobody can game it."""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import EVERYONE
from tests.security.helpers import acting, change, context_of

EXTERNALS = {"field": "kind", "op": "in", "value": ["external"]}


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


async def _members(engine: AsyncEngine, group: uuid.UUID) -> dict[uuid.UUID, bool]:
    """Active members, and whether each came from the rule."""
    async with acting(engine, None) as records:
        page = await records.list(
            "sys.membership", where={"field": "group", "op": "eq", "value": str(group)}, limit=500
        )
    return {r.values["person"]: r.values["from_rule"] for r in page.records}


async def test_members_follow_the_rule(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        before = await records.create("sys.person", {"name": f"Early {tag}", "kind": "external"})
        group = await records.create("sys.group", {"name": f"Externals {tag}", "rule": EXTERNALS})
        staff = await records.create("sys.person", {"name": f"Staff {tag}"})
        later = await records.create("sys.person", {"name": f"Later {tag}", "kind": "external"})
    members = await _members(engine, group.sys_id)
    assert members.get(before.sys_id) is True  # there before the group: found by the rule
    assert members.get(later.sys_id) is True  # made later: joins at once
    assert staff.sys_id not in members

    async with acting(engine, None) as records:
        await change(records, "sys.person", later.sys_id, {"kind": "employee"})
        await change(records, "sys.person", before.sys_id, {"active": False})
    members = await _members(engine, group.sys_id)
    assert later.sys_id not in members  # moved: left in the same transaction
    assert before.sys_id not in members  # inactive: left every rule group


async def test_a_changed_rule_moves_people_at_once(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    staff = {"field": "kind", "op": "eq", "value": "employee"}
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": f"Vendor {tag}", "kind": "external"})
        group = await records.create("sys.group", {"name": f"Moving {tag}", "rule": staff})
        assert person.sys_id not in await _members_in(records, group.sys_id)
        await change(records, "sys.group", group.sys_id, {"rule": EXTERNALS})
        assert person.sys_id in await _members_in(records, group.sys_id)
        # Without a rule the group is kept by hand; its members stay.
        await change(records, "sys.group", group.sys_id, {"rule": None})
    members = await _members(engine, group.sys_id)
    assert members.get(person.sys_id) is False


async def test_service_accounts_are_never_found_by_a_rule(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    services = {"field": "kind", "op": "eq", "value": "service"}
    async with acting(engine, None) as records:
        robot = await records.create("sys.person", {"name": f"Robot {tag}", "kind": "service"})
        group = await records.create("sys.group", {"name": f"Robots {tag}", "rule": services})
    assert robot.sys_id not in await _members(engine, group.sys_id)


async def _members_in(records: Any, group: uuid.UUID) -> set[uuid.UUID]:
    page = await records.list(
        "sys.membership", where={"field": "group", "op": "eq", "value": str(group)}, limit=500
    )
    return {r.values["person"] for r in page.records}


@pytest.mark.parametrize(
    "rule",
    [
        {"field": "name", "op": "contains", "value": "boss"},  # people change their name
        {"field": "email", "op": "ends_with", "value": "@example.com"},
        {"field": "sys_id", "op": "eq", "value": "@me"},
        {"field": "kind", "op": "in", "value": ["@my_groups"]},
        {"field": "created_by.name", "op": "eq", "value": "x"},
        {"field": "colour", "op": "eq", "value": "red"},
    ],
)
async def test_rules_people_could_game_are_refused(engine: AsyncEngine, rule: Any) -> None:
    with pytest.raises(Problem) as refused:
        async with acting(engine, None) as records:
            await records.create(
                "sys.group", {"name": f"Gamed {uuid.uuid4().hex[:6]}", "rule": rule}
            )
    assert refused.value.code in ("invalid_rule", "invalid_condition")


async def test_members_of_a_rule_group_are_not_added_by_hand(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": f"Pat {tag}"})
        group = await records.create("sys.group", {"name": f"Ruled {tag}", "rule": EXTERNALS})
    with pytest.raises(Problem) as refused:
        async with acting(engine, None) as records:
            await records.create("sys.membership", {"group": group.sys_id, "person": person.sys_id})
    assert refused.value.code == "from_rule"


async def test_everyone_gets_the_user_role(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": f"New {uuid.uuid4().hex[:6]}"})
    context = await context_of(engine, person.sys_id)
    assert EVERYONE in context.access.groups
    assert context.access.of("sys.person", "read")
    assert not context.access.of("sys.person", "write")
