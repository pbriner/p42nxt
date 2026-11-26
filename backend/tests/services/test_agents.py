"""Agents: a one-time code, enrolment with its own service account and token, tasks fetched and
answered, the module told, quiet ones marked."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.hooks import HOOKS
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.tokens.service import use_token
from p42cloud.services.agents import service as agents_service
from p42cloud.services.agents.manifest import HOOKS as agent_hooks
from p42cloud.services.agents.service import enrol, mark_quiet, new_code, queue, report, tasks_for


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def test_an_agent_enrols_once_fetches_tasks_and_answers(engine: AsyncEngine) -> None:
    name = f"pve-{uuid.uuid4().hex[:6]}"
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            code, _ = await new_code(conn, name)
            made = await enrol(conn, code, "0.1.0", "pve-vm-3", "10.0.0.9")
        with pytest.raises(Problem):  # a code works once
            async with transaction(engine, "sys") as conn:
                await enrol(conn, code, "0.1.0", "other", None)
        use = await use_token(engine, made["token"])
        agent = uuid.UUID(made["agent"])
        async with transaction(engine, "sys") as conn:
            task = await queue(conn, agent, "cmdb", "shelly_status", {"address": "10.0.0.50"})
            fetched = await tasks_for(conn, use.person, "0.1.1", "10.0.0.9")
            assert [t["task"] for t in fetched] == [str(task)]
            assert await tasks_for(conn, use.person, None, None) == []  # running now
            await report(conn, use.person, task, True, {"switch:0": {"output": True}})
            row = (
                await conn.execute(
                    text("SELECT state, result FROM sys.agent_task WHERE sys_id = :t"), {"t": task}
                )
            ).first()
            told = await conn.scalar(
                text(
                    "SELECT count(*) FROM sys.event WHERE type = 'sys.agent_task_done'"
                    " AND record_id = :t"
                ),
                {"t": str(task)},
            )
            await conn.execute(
                text("UPDATE sys.agent SET last_seen = :t WHERE sys_id = :a"),
                {"t": datetime.now(UTC) - timedelta(hours=1), "a": agent},
            )
            assert await mark_quiet(conn) >= 1
    assert row is not None and row.state == "done" and row.result["switch:0"]["output"] is True
    assert told == 1


async def test_a_replaced_or_turned_off_agent_loses_its_token(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = f"pi-{uuid.uuid4().hex[:6]}"
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            first = await enrol(conn, (await new_code(conn, name))[0], "0.1.0", "pi", None)
        async with transaction(engine, "sys") as conn:
            second = await enrol(conn, (await new_code(conn, name))[0], "0.1.0", "pi", None)
        with pytest.raises(Problem):  # enrolled again: the first token is gone
            await use_token(engine, first["token"])
        use = await use_token(engine, second["token"])
        async with transaction(engine, "sys") as conn:
            agent = uuid.UUID(second["agent"])
            one = await queue(conn, agent, "cmdb", "shelly_status", {"address": "10.0.0.7"})
            again = await queue(conn, agent, "cmdb", "shelly_status", {"address": "10.0.0.7"})
            assert one == again  # still waiting: not queued twice
            monkeypatch.setattr(agents_service, "TASK_LIFE", timedelta(seconds=-1))
            await mark_quiet(conn)
            state = await conn.scalar(
                text("SELECT state FROM sys.agent_task WHERE sys_id = :id"), {"id": one}
            )
    assert state == "failed"  # nobody answered in time
    assert use.person is not None


async def test_turning_an_agent_off_stops_its_token(engine: AsyncEngine) -> None:
    HOOKS.add(*agent_hooks)  # as the core connects them at start
    name = f"nas-{uuid.uuid4().hex[:6]}"
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            made = await enrol(conn, (await new_code(conn, name))[0], "0.1.0", "nas", None)
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            agent = uuid.UUID(made["agent"])
            current = await records.read("sys.agent", agent)
            await records.update("sys.agent", agent, {"active": False}, current.etag)
        with pytest.raises(Problem):
            await use_token(engine, made["token"])
