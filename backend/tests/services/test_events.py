"""Events: published with the change, delivered by the worker to each module listening, once."""

import asyncio
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.lifecycle import switch_off, switch_on
from p42cloud.modules.manifest import Module
from p42cloud.sdk.client import Client
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.access.roles import Grant, Role
from p42cloud.services.events.models import EventRun, EventType, Subscription
from p42cloud.services.events.outbox import publish
from p42cloud.services.events.service import (
    STUCK,
    deliver,
    recover,
    try_again,
    write_events,
)
from p42cloud.services.jobs import service as jobs
from p42cloud.services.jobs.models import Job, JobRun
from p42cloud.services.jobs.service import account_of
from tests.security.helpers import acting

LATER = timedelta(days=1)  # far enough for any wait to be over
FAIL: set[str] = set()  # names whose handling fails
HEARD: list[tuple[uuid.UUID, str, dict[str, Any], str | None]] = []


async def _handle(run: EventRun) -> None:
    """Writes a record of the module's own, then maybe fails: nothing of it may be kept."""
    module = run.type.split(".")[0]
    name = str(run.data.get("name", ""))
    await Client(run.records).create(f"{module}.item", {"name": name})
    if name in FAIL:
        raise RuntimeError(f"can't handle {name}")


async def _on_job(run: EventRun) -> None:
    HEARD.append((run.records.situation.person, run.type, run.data, run.about_table))


def _module(module_id: str) -> Module:
    return Module(
        id=module_id,
        publisher="test",
        name="Listener",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(Table("item", "Item", {"name": String("Name", max_length=40)}),),
        roles=(
            Role(
                f"{module_id}.writer", "Writer", (Grant(f"{module_id}.item", ("read", "create")),)
            ),
        ),
        events=(EventType("thing_done", "A thing is done", about=f"{module_id}.item"),),
        subscriptions=(
            Subscription(f"{module_id}.thing_done", _handle, retries=1),
            Subscription("sys.job_failed", _on_job),
            Subscription("sys.job_recovered", _on_job),
        ),
    )


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


@pytest.fixture
async def listener(owner_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]]) -> str:
    module_id = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(module_id)])
    FAIL.clear()
    HEARD.clear()
    return module_id


async def _settle(engine: AsyncEngine) -> None:
    """Delivers whatever is due, so each test starts from a quiet queue."""
    while await deliver(engine, datetime.now(UTC) + LATER) is not None:
        pass


async def _publish(engine: AsyncEngine, module: str, name: str) -> uuid.UUID:
    async with acting(engine, None) as records:
        return await Client(records).publish(f"{module}.thing_done", data={"name": name})


async def _items(engine: AsyncEngine, module: str) -> list[dict[str, Any]]:
    async with acting(engine, None) as records:
        page = await records.list(f"{module}.item", limit=100)
    return [dict(r.values) for r in page.records]


async def _deliveries(engine: AsyncEngine, event: uuid.UUID) -> list[Any]:
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            return list(
                await conn.execute(
                    text(
                        "SELECT sys_id, subscription, state, attempts, message"
                        " FROM sys.event_delivery WHERE event = :e"
                    ),
                    {"e": event},
                )
            )


async def test_an_event_is_delivered_once_after_its_change_is_kept(
    engine: AsyncEngine, listener: str
) -> None:
    await _settle(engine)
    with pytest.raises(RuntimeError):
        async with acting(engine, None) as records:
            await Client(records).publish(f"{listener}.thing_done", data={"name": "undone"})
            raise RuntimeError("the change is undone")
    assert await deliver(engine) is None  # undone with it
    event = await _publish(engine, listener, "first")
    assert await deliver(engine) == "done"
    assert await deliver(engine) is None  # once
    [delivery] = await _deliveries(engine, event)
    assert (delivery.subscription, delivery.state) == (f"{listener}:{listener}.thing_done", "done")
    [item] = await _items(engine, listener)
    # Written as the module's service account, by the module's own role.
    assert item["name"] == "first" and item["created_by"] == account_of(listener)


async def test_two_workers_deliver_an_event_once(engine: AsyncEngine, listener: str) -> None:
    await _settle(engine)
    await _publish(engine, listener, "raced")
    states = await asyncio.gather(deliver(engine), deliver(engine))
    await _settle(engine)
    assert sorted(states, key=str) == [None, "done"]
    assert [i["name"] for i in await _items(engine, listener)] == ["raced"]


async def test_a_failing_handler_keeps_nothing_and_is_tried_again(
    engine: AsyncEngine, listener: str
) -> None:
    await _settle(engine)
    async with acting(engine, None) as records:
        admin = (await records.create("sys.person", {"name": f"Admin {listener}"})).sys_id
        await records.create("sys.membership", {"person": str(admin), "group": str(ADMINISTRATORS)})
    FAIL.add("broken")
    event = await _publish(engine, listener, "broken")
    assert await deliver(engine) == "pending"
    [delivery] = await _deliveries(engine, event)
    assert (delivery.state, delivery.attempts) == ("pending", 1)
    assert "can't handle broken" in delivery.message
    assert await deliver(engine) is None  # waits before trying again
    assert await deliver(engine, datetime.now(UTC) + LATER) == "failed"  # one retry
    assert await _items(engine, listener) == []  # nothing of the tries was kept
    async with acting(engine, admin) as records:
        told = await records.list(
            "sys.notification", where={"field": "kind", "op": "eq", "value": "sys.event_failed"}
        )
    assert told.total == 1

    FAIL.clear()
    async with acting(engine, admin) as records:
        await try_again(records.conn, delivery.sys_id)
    assert await deliver(engine) == "done"
    assert [i["name"] for i in await _items(engine, listener)] == ["broken"]
    with pytest.raises(Problem):  # only a failed one
        async with acting(engine, admin) as records:
            await try_again(records.conn, delivery.sys_id)


async def test_a_switched_off_module_gets_its_events_when_switched_on(
    engine: AsyncEngine, owner_engine: AsyncEngine, listener: str
) -> None:
    await _settle(engine)
    with working_as(Context(channel="test")):
        await switch_off(owner_engine, listener)
    try:
        event = await _publish(engine, listener, "later")
        assert await deliver(engine) is None
    finally:
        with working_as(Context(channel="test")):
            await switch_on(owner_engine, listener)
    assert await deliver(engine) == "done"
    assert [d.state for d in await _deliveries(engine, event)] == ["done"]


async def test_a_delivery_a_stopped_worker_left_is_taken_up_again(
    engine: AsyncEngine, listener: str
) -> None:
    await _settle(engine)
    event = await _publish(engine, listener, "stopped")
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "UPDATE sys.event_delivery SET state = 'working', attempts = 1,"
                    " started = :t WHERE event = :e"
                ),
                {"t": datetime.now(UTC) - STUCK - timedelta(minutes=1), "e": event},
            )
    assert await recover(engine) == 1
    assert await deliver(engine) == "done"
    assert [i["name"] for i in await _items(engine, listener)] == ["stopped"]


async def test_only_known_types_and_small_data_are_published(
    engine: AsyncEngine, listener: str
) -> None:
    with pytest.raises(Problem) as unknown:
        async with acting(engine, None) as records:
            await publish(records.conn, f"{listener}.nothing")
    assert unknown.value.status == 404
    with pytest.raises(Problem) as big:
        async with acting(engine, None) as records:
            await publish(records.conn, f"{listener}.thing_done", data={"x": "y" * 20_000})
    assert big.value.status == 422


@pytest.mark.parametrize(
    ("types", "subscriptions", "code"),
    [
        ((EventType("Bad name", "X"),), (), "bad_name"),
        ((EventType("x", "X", about="nowhere"),), (), "bad_table"),
        ((EventType("x", "X"), EventType("x", "Y")), (), "duplicate"),
        ((), (Subscription("no-module", _on_job),), "bad_event"),
        ((), (Subscription("sys.job_failed", _on_job, retries=99),), "bad_retries"),
    ],
)
async def test_events_are_checked(
    owner_engine: AsyncEngine,
    types: tuple[EventType, ...],
    subscriptions: tuple[Subscription, ...],
    code: str,
) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_events(conn, "zz", types, subscriptions)
    assert code in [e.code for e in refused.value.errors]


async def test_a_scheduled_job_failing_and_recovering_is_published(
    engine: AsyncEngine, listener: str
) -> None:
    await _settle(engine)
    name = f"flaky{uuid.uuid4().hex[:6]}"
    full = f"sys.{name}"
    outcome = {"fail": True}

    async def flaky(run: JobRun) -> str:
        if outcome["fail"]:
            raise RuntimeError("down")
        return "fine"

    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "INSERT INTO sys.job (name, module, label, timeout, retries, custom)"
                    " VALUES (:n, 'sys', '{\"en\": \"Flaky\"}', 60, 0, false);"
                ),
                {"n": full},
            )
            await conn.execute(
                text(
                    "INSERT INTO sys.schedule (name, module, label, job, cron, time_zone, missed,"
                    " active, custom) VALUES (:n, 'sys', '{\"en\": \"Flaky\"}', :n,"
                    " '0 3 * * *', 'UTC', 'skip', true, false)"
                ),
                {"n": full},
            )
    jobs.JOBS[full] = ("sys", Job(name, "Flaky", flaky, retries=0))

    async def run_once() -> None:
        with working_as(Context(channel="test", access=EVERYTHING)):
            async with transaction(engine, "sys") as conn:
                await jobs.queue(conn, full, full, "manual")
        while (run := await jobs.claim(engine, "test")) is not None:
            await jobs.execute(engine, run)

    try:
        await run_once()
        outcome["fail"] = False
        await run_once()
        await run_once()  # working still: nothing new to say
    finally:
        jobs.JOBS.pop(full, None)
    await _settle(engine)
    mine = [
        (t, d["message"], about)
        for who, t, d, about in HEARD
        if who == account_of(listener) and d["schedule"] == full
    ]
    assert mine == [
        ("sys.job_failed", "RuntimeError: down", "sys.schedule"),
        ("sys.job_recovered", None, "sys.schedule"),
    ]
