"""Jobs and schedules: cron, declarations, and the worker's queue."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.types import Invalid
from p42cloud.kernel.config import Settings, get_settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.services.jobs import service
from p42cloud.services.jobs.cron import parse
from p42cloud.services.jobs.models import Job, JobRun, Schedule
from p42cloud.services.jobs.service import JOBS, claim, execute, recover, run_job, tick, write_jobs
from tests.security.helpers import acting, change

NOON = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("cron", "zone", "after", "expected"),
    [
        ("*/5 * * * *", "UTC", NOON, datetime(2026, 11, 14, 12, 5, tzinfo=UTC)),
        ("17 3 * * *", "Europe/Zurich", NOON, datetime(2026, 11, 15, 2, 17, tzinfo=UTC)),
        ("0 9 * * 1-5", "UTC", NOON, datetime(2026, 11, 16, 9, 0, tzinfo=UTC)),  # Monday
        ("0 0 29 2 *", "UTC", NOON, datetime(2028, 2, 29, 0, 0, tzinfo=UTC)),
        ("30 8 1,15 * 0", "UTC", NOON, datetime(2026, 11, 15, 8, 30, tzinfo=UTC)),  # either day
    ],
)
def test_cron_finds_the_next_moment(
    cron: str, zone: str, after: datetime, expected: datetime
) -> None:
    assert parse(cron).next_after(after, zone) == expected


@pytest.mark.parametrize(
    "cron", ["* * * *", "60 * * * *", "*/0 * * * *", "a * * * *", "5-1 * * * *"]
)
def test_cron_refuses_what_it_can_t_read(cron: str) -> None:
    with pytest.raises(Invalid):
        parse(cron)


async def _nothing(run: JobRun) -> str:
    return "done"


@pytest.mark.parametrize(
    ("jobs", "schedules", "code"),
    [
        ((Job("Bad", "X", _nothing),), (), "bad_name"),
        ((Job("x", "X", _nothing, timeout=0),), (), "bad_timeout"),
        ((Job("x", "X", _nothing),), (Schedule("s", "S", "other", "* * * * *"),), "unknown_job"),
        ((Job("x", "X", _nothing),), (Schedule("s", "S", "x", "every day"),), "bad_cron"),
        (
            (Job("x", "X", _nothing),),
            (Schedule("s", "S", "x", "* * * * *", "Mars/Base"),),
            "bad_time_zone",
        ),
    ],
)
async def test_jobs_are_checked(
    owner_engine: AsyncEngine, jobs: tuple[Job, ...], schedules: tuple[Schedule, ...], code: str
) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_jobs(conn, "zz", jobs, schedules)
    assert code in [e.code for e in refused.value.errors]


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def _quiet_queue(engine: AsyncEngine) -> None:
    """Earlier tests' runs out of the way: these tests look at their own."""
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "UPDATE sys.job_run SET state = 'skipped' WHERE state IN ('queued', 'running')"
                )
            )


async def _test_job(
    engine: AsyncEngine, name: str, work: object, timeout: int = 60, retries: int = 0
) -> str:
    """A core job for this test only: its record, and its code in the registry."""
    full = f"sys.{name}"
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "INSERT INTO sys.job (name, module, label, timeout, retries, custom)"
                    " VALUES (:n, 'sys', '{\"en\": \"Test\"}', :t, :r, false)"
                ),
                {"n": full, "t": timeout, "r": retries},
            )
    JOBS[full] = ("sys", Job(name, "Test", work, timeout=timeout, retries=retries))  # type: ignore[arg-type]
    return full


async def test_a_due_schedule_runs_its_job_once(engine: AsyncEngine) -> None:
    await _quiet_queue(engine)
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text("UPDATE sys.schedule SET next_run = :t WHERE name = 'sys.repair_groups'"),
                {"t": datetime.now(UTC) - timedelta(minutes=1)},
            )
    queued = await tick(engine)
    again = await tick(engine)  # the next slot isn't due yet
    run = await claim(engine, "test")
    assert run is not None and run.job == "sys.repair_groups"
    state = await execute(engine, run)
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            last = await conn.scalar(
                text("SELECT last_state FROM sys.schedule WHERE name = 'sys.repair_groups'")
            )
            said = await conn.scalar(
                text("SELECT message FROM sys.job_run WHERE sys_id = :i"), {"i": run.sys_id}
            )
    assert (queued, again, state, last) == (1, 0, "succeeded", "succeeded")
    assert said is not None and said.endswith("groups checked.")


async def test_a_failing_job_is_tried_again_later(engine: AsyncEngine) -> None:
    await _quiet_queue(engine)

    async def fails(run: JobRun) -> str:
        raise ValueError("the outside system said no")

    name = await _test_job(engine, f"fails{uuid.uuid4().hex[:6]}", fails, retries=1)
    state, said = await run_job(engine, name)
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            retry = (
                await conn.execute(
                    text(
                        "SELECT attempt, not_before, trigger FROM sys.job_run"
                        " WHERE job = :j AND state = 'queued'"
                    ),
                    {"j": name},
                )
            ).first()
    assert (state, said) == ("failed", "ValueError: the outside system said no")
    assert retry is not None and (retry.attempt, retry.trigger) == (2, "retry")
    assert retry.not_before > datetime.now(UTC)  # waits before trying again


async def test_a_job_that_takes_too_long_is_stopped(engine: AsyncEngine) -> None:
    await _quiet_queue(engine)

    async def slow(run: JobRun) -> str:
        await asyncio.sleep(5)
        return "never"

    name = await _test_job(engine, f"slow{uuid.uuid4().hex[:6]}", slow, timeout=1)
    assert await run_job(engine, name) == ("failed", "Took longer than 1 seconds.")


async def test_a_schedule_doesn_t_overlap_and_stopped_runs_end(engine: AsyncEngine) -> None:
    await _quiet_queue(engine)
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await service.queue(conn, "sys.repair_groups", "sys.repair_groups", "manual")
            await conn.execute(
                text("UPDATE sys.schedule SET next_run = :t WHERE name = 'sys.repair_groups'"),
                {"t": datetime.now(UTC) - timedelta(minutes=1)},
            )
    await tick(engine)
    run = await claim(engine, "gone")  # a worker that will "stop" mid-run
    ended = await recover(engine, datetime.now(UTC) + timedelta(hours=1))
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            states = [
                r.state
                for r in await conn.execute(
                    text(
                        "SELECT state FROM sys.job_run WHERE schedule = 'sys.repair_groups'"
                        " ORDER BY created DESC LIMIT 2"
                    )
                )
            ]
    assert run is not None and ended == 1
    assert sorted(states) == ["failed", "skipped"]  # the stopped run, and the slot it blocked


async def test_administrators_change_when_a_schedule_runs(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        found = (
            await records.list(
                "sys.schedule", where={"field": "name", "op": "eq", "value": "sys.repair_groups"}
            )
        ).records[0]
        with pytest.raises(Problem) as wrong:
            await change(records, "sys.schedule", found.sys_id, {"cron": "sometimes"})
        changed = await change(records, "sys.schedule", found.sys_id, {"cron": "0 4 * * *"})
        await change(records, "sys.schedule", found.sys_id, {"cron": "23 2 * * *"})
    assert wrong.value.code == "validation"
    assert changed.values["next_run"].hour == 4


async def test_the_update_check_runs_daily_and_says_when_there_is_no_registry(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, upgraded: Settings
) -> None:
    await _quiet_queue(engine)
    for name, value in (
        ("P42_DATABASE_URL", upgraded.database_url),
        ("P42_OWNER_DATABASE_URL", upgraded.owner_database_url),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("P42_ENVIRONMENT", "test")
    monkeypatch.delenv("P42_REGISTRY_URL", raising=False)
    get_settings.cache_clear()
    try:
        assert await run_job(engine, "sys.check_updates") == (
            "succeeded",
            "No module registry is set: nothing to check.",
        )
    finally:
        get_settings.cache_clear()


async def test_schedules_and_runs_link_their_job_and_schedule(engine: AsyncEngine) -> None:
    await _quiet_queue(engine)
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            run = await service.queue(conn, "sys.repair_groups", "sys.repair_groups", "manual")
            alone = await service.queue(conn, "sys.repair_groups", None, "manual")
            found = (
                await conn.execute(
                    text(
                        "SELECT r.sys_id, r.job_ref = j.sys_id AS job_linked,"
                        " r.schedule_ref = s.sys_id AS schedule_linked, r.schedule_ref IS NULL"
                        " AS no_schedule, s.job_ref = j.sys_id AS schedule_job_linked"
                        " FROM sys.job_run r, sys.job j, sys.schedule s"
                        " WHERE r.sys_id IN (:a, :b) AND j.name = 'sys.repair_groups'"
                        " AND s.name = 'sys.repair_groups'"
                    ),
                    {"a": run, "b": alone},
                )
            ).all()
    by_id = {r.sys_id: r for r in found}
    assert by_id[run].job_linked and by_id[run].schedule_linked
    assert by_id[alone].job_linked and by_id[alone].no_schedule
    assert by_id[run].schedule_job_linked


async def test_a_schedule_keeps_the_job_its_module_gave_it(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        found = (
            await records.list(
                "sys.schedule", where={"field": "name", "op": "eq", "value": "sys.repair_groups"}
            )
        ).records[0]
        with pytest.raises(Problem) as refused:
            await change(records, "sys.schedule", found.sys_id, {"job": "sys.backup"})
    assert refused.value.code == "declared_only"


async def test_a_run_written_by_plain_sql_is_linked_too(upgraded: Settings) -> None:
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            made = await conn.scalar(
                text(
                    "INSERT INTO sys.job_run (job, schedule, state, trigger, attempt)"
                    " VALUES ('sys.repair_groups', 'sys.repair_groups', 'skipped', 'schedule', 1)"
                    " RETURNING sys_id"
                )
            )
            linked = (
                await conn.execute(
                    text(
                        "SELECT job_ref IS NOT NULL AS job, schedule_ref IS NOT NULL AS schedule"
                        " FROM sys.job_run WHERE sys_id = :id"
                    ),
                    {"id": made},
                )
            ).one()
    await owner.dispose()
    assert linked.job and linked.schedule
