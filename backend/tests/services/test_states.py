"""States with allowed moves: where a record starts, the only moves, moves for some roles or
only under a condition, and each move as a button."""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.security.access.roles import Grant, Role
from p42cloud.services.states.models import Flow, Transition
from p42cloud.services.states.service import write_flows
from tests.security.helpers import acting, change

ALL = ("read", "create", "write", "delete", "restore")
STATES = {"new": "New", "doing": "Doing", "done": "Done", "cancelled": "Cancelled"}


def _module(module_id: str) -> Module:
    task = Table(
        "task",
        "Task",
        {
            "title": String("Title", max_length=60),
            "note": String("Note", max_length=60),
            "state": Choice("State", STATES, required=True, default="new"),
        },
    )
    table = f"{module_id}.task"
    return Module(
        id=module_id,
        publisher="test",
        name="Tasks",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(task,),
        roles=(
            Role(f"{module_id}.worker", "Worker", (Grant(table, ALL),)),
            Role(f"{module_id}.boss", "Boss", (Grant(table, ALL),)),
        ),
        flows=(
            Flow(
                table,
                "state",
                initial="new",
                transitions=(
                    Transition("start", "Start", "new", "doing"),
                    Transition("finish", "Mark done", "doing", "done"),
                    Transition("cancel", "Cancel", None, "cancelled", roles=(f"{module_id}.boss",)),
                    Transition(
                        "reopen",
                        "Reopen",
                        "done",
                        "doing",
                        condition={"field": "note", "op": "not_empty"},
                    ),
                ),
            ),
        ),
    )


@pytest.fixture(scope="module")
async def app(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def setup(
    owner_engine: AsyncEngine, app: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> dict[str, Any]:
    module_id = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(module_id)])
    found: dict[str, Any] = {"table": f"{module_id}.task", "module": module_id}
    async with acting(app, None) as records:
        for name in ("worker", "boss"):
            role = await records.conn.scalar(
                text("SELECT sys_id FROM sys.role WHERE name = :n"), {"n": f"{module_id}.{name}"}
            )
            group = await records.create("sys.group", {"name": f"{name} {uuid.uuid4().hex[:6]}"})
            await records.create("sys.group_role", {"group": group.sys_id, "role": role})
            person = await records.create("sys.person", {"name": f"{name} {module_id}"})
            await records.create("sys.membership", {"group": group.sys_id, "person": person.sys_id})
            found[name] = person.sys_id
    return found


async def _move(app: AsyncEngine, who: Any, table: str, task: Any, values: dict[str, Any]) -> Any:
    async with acting(app, who) as records:
        return (await change(records, table, task, values)).values["state"]


async def _refused(app: AsyncEngine, who: Any, table: str, task: Any, state: str) -> str:
    with pytest.raises(Problem) as refused:
        await _move(app, who, table, task, {"state": state})
    (error,) = refused.value.errors
    assert (error.field, error.code) == ("state", "not_allowed")
    return error.message


async def test_a_record_starts_where_its_flow_begins(
    setup: dict[str, Any], app: AsyncEngine
) -> None:
    table = setup["table"]
    async with acting(app, setup["worker"]) as records:
        made = await records.create(table, {"title": "Paint"})
    assert made.values["state"] == "new"
    with pytest.raises(Problem) as refused:
        async with acting(app, setup["worker"]) as records:
            await records.create(table, {"title": "Skip ahead", "state": "done"})
    assert refused.value.errors[0].message == "A new record starts in New."


async def test_a_state_moves_only_along_a_transition(
    setup: dict[str, Any], app: AsyncEngine
) -> None:
    table, worker = setup["table"], setup["worker"]
    async with acting(app, worker) as records:
        task = (await records.create(table, {"title": "Paint"})).sys_id
    assert await _refused(app, worker, table, task, "done") == "From New it can't go to Done."
    assert await _move(app, worker, table, task, {"state": "doing"}) == "doing"
    assert await _move(app, worker, table, task, {"title": "Paint it"}) == "doing"  # not a move
    assert await _move(app, worker, table, task, {"state": "done"}) == "done"


async def test_some_moves_are_for_some_roles_or_some_records(
    setup: dict[str, Any], app: AsyncEngine
) -> None:
    table, worker, boss = setup["table"], setup["worker"], setup["boss"]
    async with acting(app, worker) as records:
        task = (await records.create(table, {"title": "Paint"})).sys_id
    said = await _refused(app, worker, table, task, "cancelled")
    assert said == "From New it can't go to Cancelled now, or not by you."
    assert await _move(app, boss, table, task, {"state": "cancelled"}) == "cancelled"  # from any

    async with acting(app, worker) as records:
        other = (await records.create(table, {"title": "Wash"})).sys_id
    await _move(app, worker, table, other, {"state": "doing"})
    await _move(app, worker, table, other, {"state": "done"})
    await _refused(app, worker, table, other, "doing")  # reopening needs a note
    await _move(app, worker, table, other, {"note": "Missed a spot"})
    assert await _move(app, worker, table, other, {"state": "doing"}) == "doing"


async def test_each_move_is_a_button_where_it_applies(
    setup: dict[str, Any], app: AsyncEngine
) -> None:
    async with acting(app, None) as records:
        found = await records.list(
            "sys.ui_action",
            where={"field": "module", "op": "eq", "value": setup["module"]},
            limit=20,
        )
    buttons = {r.values["name"].split(".")[1]: r.values for r in found.records}
    assert sorted(buttons) == ["move_cancel", "move_finish", "move_reopen", "move_start"]
    assert buttons["move_start"]["set"] == {"state": "doing"}
    assert buttons["move_start"]["condition"] == {"field": "state", "op": "eq", "value": "new"}
    assert buttons["move_cancel"]["condition"] == {
        "field": "state",
        "op": "ne",
        "value": "cancelled",
    }


@pytest.mark.parametrize(
    ("flow", "code"),
    [
        (Flow("zz.task", "state", "new", ()), "not_its_table"),
        (Flow("{m}.task", "title", "new", ()), "not_a_choice"),
        (Flow("{m}.task", "state", "nowhere", ()), "unknown_state"),
        (Flow("{m}.task", "state", "new", (Transition("x", "X", "new", "gone"),)), "unknown_state"),
        (Flow("{m}.task", "state", "new", (Transition("x", "X", "new", "new"),)), "no_move"),
    ],
)
async def test_flows_are_checked(
    setup: dict[str, Any], owner_engine: AsyncEngine, flow: Flow, code: str
) -> None:
    module_id = setup["module"]
    flow = Flow(flow.table.replace("{m}", module_id), flow.field, flow.initial, flow.transitions)
    with working_as(Context(channel="install")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_flows(conn, await load_catalog(conn), module_id, (flow,))
    assert code in [e.code for e in refused.value.errors]
