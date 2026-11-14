"""Writing jobs and schedules into records, the modules' service accounts, and the worker's work.

The worker claims what's due with `FOR UPDATE SKIP LOCKED`, so several workers never start the
same schedule slot or run twice. A run is one transaction as the job's module: committed when the
job returns, rolled back when it fails or takes too long; failed runs are tried again later, each
time waiting twice as long. A schedule doesn't start while its previous run is still going.
"""

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.data.types import Invalid
from p42cloud.kernel.context import EVERYTHING, SYSTEM_PERSON, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.service import access_for
from p42cloud.services.jobs.cron import parse
from p42cloud.services.jobs.models import Job, JobRun, Schedule

JOB_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_TIMEOUT = 3600
MAX_RETRIES = 10
FIRST_WAIT = timedelta(seconds=30)
MAX_MESSAGE = 4000
# Fixed IDs per module, the same in every installation.
_ACCOUNTS = uuid.UUID("6f2b1c34-7a0e-5d4f-9c21-4a8e0b7d1f42")

# The installed modules' jobs, by name: (module, job). Filled when modules are composed.
JOBS: dict[str, tuple[str, Job]] = {}


def register(module: str, jobs: tuple[Job, ...]) -> None:
    for job in jobs:
        JOBS[f"{module}.{job.name}"] = (module, job)


def account_of(module: str) -> uuid.UUID:
    """The person a module's jobs act as."""
    return uuid.uuid5(_ACCOUNTS, f"person:{module}")


def _group_of(module: str) -> uuid.UUID:
    return uuid.uuid5(_ACCOUNTS, f"group:{module}")


# ---- declaring ----


def _problems(
    module: str, jobs: tuple[Job, ...], schedules: tuple[Schedule, ...]
) -> list[FieldError]:
    found: list[FieldError] = []
    names = {j.name for j in jobs}
    if len(names) != len(jobs) or len({s.name for s in schedules}) != len(schedules):
        found.append(FieldError("jobs", "duplicate", "A name is used twice."))
    for job in jobs:
        where = f"jobs.{job.name}"
        if not JOB_NAME.match(job.name):
            found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
        if not 1 <= job.timeout <= MAX_TIMEOUT:
            found.append(FieldError(where, "bad_timeout", f"1 to {MAX_TIMEOUT} seconds."))
        if not 0 <= job.retries <= MAX_RETRIES:
            found.append(FieldError(where, "bad_retries", f"0 to {MAX_RETRIES} retries."))
    for schedule in schedules:
        where = f"schedules.{schedule.name}"
        if not JOB_NAME.match(schedule.name):
            found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
        if schedule.job not in names:
            found.append(FieldError(where, "unknown_job", f"{module} has no job {schedule.job!r}."))
        found += cron_problems(where, schedule.cron, schedule.time_zone)
    return found


def cron_problems(where: str, cron: str, zone: str) -> list[FieldError]:
    found: list[FieldError] = []
    try:
        parse(cron)
    except Invalid as error:
        found.append(FieldError(where, "bad_cron", str(error)))
    try:
        ZoneInfo(zone)
    except ZoneInfoNotFoundError, ValueError:
        found.append(FieldError(where, "bad_time_zone", f"No time zone {zone!r}."))
    return found


async def write_jobs(
    conn: AsyncConnection, module: str, jobs: tuple[Job, ...], schedules: tuple[Schedule, ...]
) -> None:
    """Brings the module's jobs and schedules to its declaration (as the owner). A schedule's
    time and whether it's active stay as administrators set them."""
    found = _problems(module, jobs, schedules)
    if found:
        raise Problem(422, "invalid_jobs", "The jobs can't be used.", errors=found)
    for job in jobs:
        values = {
            "name": f"{module}.{job.name}",
            "module": module,
            "label": _label(job.label),
            "timeout": job.timeout,
            "retries": job.retries,
        }
        changed = await conn.execute(
            text(
                "UPDATE sys.job SET module = :module, label = CAST(:label AS jsonb),"
                " timeout = :timeout, retries = :retries, deleted = NULL WHERE name = :name"
            ),
            values,
        )
        if changed.rowcount == 0:
            await conn.execute(
                text(
                    "INSERT INTO sys.job (name, module, label, timeout, retries, custom)"
                    " VALUES (:name, :module, CAST(:label AS jsonb), :timeout, :retries, false)"
                ),
                values,
            )
    now = datetime.now(UTC)
    for schedule in schedules:
        values = {
            "name": f"{module}.{schedule.name}",
            "module": module,
            "label": _label(schedule.label),
            "job": f"{module}.{schedule.job}",
            "cron": schedule.cron,
            "zone": schedule.time_zone,
            "missed": schedule.missed,
            "next": parse(schedule.cron).next_after(now, schedule.time_zone),
        }
        changed = await conn.execute(
            text(
                "UPDATE sys.schedule SET module = :module, label = CAST(:label AS jsonb),"
                " job = :job, missed = :missed, deleted = NULL WHERE name = :name"
            ),
            values,
        )
        if changed.rowcount == 0:
            await conn.execute(
                text(
                    "INSERT INTO sys.schedule (name, module, label, job, cron, time_zone, missed,"
                    " active, next_run, custom) VALUES (:name, :module, CAST(:label AS jsonb),"
                    " :job, :cron, :zone, :missed, true, :next, false)"
                ),
                values,
            )
    for table, kept in (
        ("sys.job", [f"{module}.{j.name}" for j in jobs]),
        ("sys.schedule", [f"{module}.{s.name}" for s in schedules]),
    ):
        await conn.execute(
            text(
                f"UPDATE {table} SET deleted = now() WHERE module = :module"  # noqa: S608
                " AND NOT custom AND deleted IS NULL AND NOT name = ANY(:kept)"
            ),
            {"module": module, "kept": kept},
        )
    if jobs and module != "sys":  # the core's jobs run as the system
        await _service_account(conn, module)


def _label(value: str | dict[str, str]) -> str:
    return json.dumps({"en": value} if isinstance(value, str) else value)


async def _service_account(conn: AsyncConnection, module: str) -> None:
    """The person the module's jobs act as, in a group holding the module's roles."""
    person, group = account_of(module), _group_of(module)
    await conn.execute(
        text(
            "INSERT INTO sys.person (sys_id, name, kind, active, emergency_access)"
            " VALUES (:id, :name, 'service', true, false) ON CONFLICT (sys_id) DO NOTHING"
        ),
        {"id": person, "name": f"{module} jobs"},
    )
    await conn.execute(
        text(
            'INSERT INTO sys."group" (sys_id, name, description, assignable)'
            " VALUES (:id, :name, :description, false) ON CONFLICT (sys_id) DO NOTHING"
        ),
        {
            "id": group,
            "name": f"{module} jobs",
            "description": f"What the {module} module's jobs may do: its own roles.",
        },
    )
    await conn.execute(
        text(
            'INSERT INTO sys.membership ("group", person, from_rule) SELECT :g, :p, false'
            " WHERE NOT EXISTS"
            ' (SELECT 1 FROM sys.membership WHERE "group" = :g AND person = :p AND deleted IS NULL)'
        ),
        {"g": group, "p": person},
    )
    await conn.execute(
        text(
            'INSERT INTO sys.group_role ("group", role) SELECT :g, r.sys_id FROM sys.role r'
            " WHERE r.module = :m AND NOT r.custom AND r.deleted IS NULL AND NOT EXISTS"
            ' (SELECT 1 FROM sys.group_role gr WHERE gr."group" = :g AND gr.role = r.sys_id'
            " AND gr.deleted IS NULL)"
        ),
        {"g": group, "m": module},
    )


# ---- the worker ----


def _system() -> Context:
    return Context(channel="worker", access=EVERYTHING)


async def _open_run(conn: AsyncConnection, schedule: str) -> bool:
    return bool(
        await conn.scalar(
            text(
                "SELECT true FROM sys.job_run WHERE schedule = :s AND deleted IS NULL"
                " AND state IN ('queued', 'running') LIMIT 1"
            ),
            {"s": schedule},
        )
    )


async def tick(engine: AsyncEngine, now: datetime | None = None) -> int:
    """Queues a run for each due schedule (of a module switched on); returns how many."""
    now = now or datetime.now(UTC)
    queued = 0
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            due = list(
                await conn.execute(
                    text(
                        "SELECT s.sys_id, s.name, s.job, s.cron, s.time_zone, s.missed, s.next_run"
                        " FROM sys.schedule s JOIN sys.module m ON m.name = s.module"
                        " WHERE s.deleted IS NULL AND s.active AND m.enabled AND m.deleted IS NULL"
                        " AND (s.next_run IS NULL OR s.next_run <= :now)"
                        " FOR UPDATE OF s SKIP LOCKED"
                    ),
                    {"now": now},
                )
            )
            for row in due:
                following = parse(row.cron).next_after(now, row.time_zone)
                missed = row.next_run is not None and now - row.next_run > timedelta(minutes=5)
                state = None
                if row.next_run is not None and not (missed and row.missed == "skip"):
                    busy = await _open_run(conn, row.name)
                    state = "skipped" if busy else "queued"
                    await conn.execute(
                        text(
                            "INSERT INTO sys.job_run (job, schedule, state, trigger, attempt,"
                            " not_before, finished, message) VALUES (:job, :schedule, :state,"
                            " 'schedule', 1, :now, :finished, :message)"
                        ),
                        {
                            "job": row.job,
                            "schedule": row.name,
                            "state": state,
                            "now": now,
                            "finished": now if busy else None,
                            "message": "The previous run was still going." if busy else None,
                        },
                    )
                    queued += 0 if busy else 1
                await conn.execute(
                    text(
                        "UPDATE sys.schedule SET next_run = :next,"
                        " last_state = CASE WHEN :state = 'skipped' THEN 'skipped'"
                        " ELSE last_state END"
                        " WHERE sys_id = :id"
                    ),
                    {"next": following, "id": row.sys_id, "state": state},
                )
    return queued


async def queue(conn: AsyncConnection, job: str, schedule: str | None, trigger: str) -> uuid.UUID:
    """A run to start as soon as a worker is free (`Run now`)."""
    found = await conn.scalar(
        text(
            "INSERT INTO sys.job_run (job, schedule, state, trigger, attempt, not_before)"
            " VALUES (:job, :schedule, 'queued', :trigger, 1, now()) RETURNING sys_id"
        ),
        {"job": job, "schedule": schedule, "trigger": trigger},
    )
    if not isinstance(found, uuid.UUID):
        raise RuntimeError("no run made")
    return found


async def claim(engine: AsyncEngine, worker: str, now: datetime | None = None) -> Any | None:
    """The next queued run, now running on this worker; None if there's nothing to do."""
    now = now or datetime.now(UTC)
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            return (
                await conn.execute(
                    text(
                        "UPDATE sys.job_run SET state = 'running', started = :now, worker = :worker"
                        " WHERE sys_id = (SELECT sys_id FROM sys.job_run WHERE state = 'queued'"
                        " AND deleted IS NULL AND (not_before IS NULL OR not_before <= :now)"
                        " ORDER BY not_before NULLS FIRST, created LIMIT 1 FOR UPDATE SKIP LOCKED)"
                        " RETURNING sys_id, job, schedule, attempt"
                    ),
                    {"now": now, "worker": worker},
                )
            ).first()


async def _settings_of(engine: AsyncEngine, job: str) -> tuple[int, int, bool]:
    """A job's time limit and retries as its record has them, and whether its module is on."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT j.timeout, j.retries, m.enabled AND m.deleted IS NULL AS on"
                        " FROM sys.job j JOIN sys.module m ON m.name = j.module"
                        " WHERE j.name = :j AND j.deleted IS NULL"
                    ),
                    {"j": job},
                )
            ).first()
    return (row.timeout, row.retries, bool(row.on)) if row else (300, 0, False)


def _said(error: BaseException) -> str:
    if isinstance(error, Problem):
        return f"{error.title} {error.detail or ''}".strip()
    return f"{type(error).__name__}: {error}"[:MAX_MESSAGE]


async def _run_as(engine: AsyncEngine, module: str, run: Any, job: Job) -> str | None:
    """The job's code, in one transaction as the module's service account (the core: itself)."""
    person = SYSTEM_PERSON if module == "sys" else account_of(module)
    access = EVERYTHING
    if module != "sys":
        with working_as(Context(channel="worker", person_id=person)):
            async with transaction(engine, "sys") as conn:
                access = await access_for(conn, person)
    context = Context(
        channel=f"job:{run.job}", person_id=person, request_id=run.sys_id, access=access
    )
    with working_as(context):
        # Like a request: the records service switches to each table's module role itself.
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            return await job.run(
                JobRun(records, run.sys_id, run.job, run.attempt, datetime.now(UTC))
            )


async def run_job(engine: AsyncEngine, job: str) -> tuple[str, str | None]:
    """Runs a job at once, as the worker would (for tests and the command line): its state and
    what it said."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            queued = await queue(conn, job, None, "manual")
    while (run := await claim(engine, "now")) is not None:
        state = await execute(engine, run)
        if run.sys_id == queued:
            with working_as(_system()):
                async with transaction(engine, "sys") as conn:
                    said = await conn.scalar(
                        text("SELECT message FROM sys.job_run WHERE sys_id = :id"), {"id": queued}
                    )
            return state, said
    raise Problem(409, "not_run", "The run wasn't picked up.")


async def execute(engine: AsyncEngine, run: Any) -> str:
    """Runs a claimed run to its end; returns its state."""
    timeout, retries, on = await _settings_of(engine, run.job)
    found = JOBS.get(run.job)
    state, message = "succeeded", None
    if found is None or not on:
        state, message = "skipped", "The job's module isn't there or is switched off."
    else:
        module, job = found
        try:
            message = await asyncio.wait_for(_run_as(engine, module, run, job), timeout)
        except TimeoutError:
            state, message = "failed", f"Took longer than {timeout} seconds."
        except Exception as error:  # whatever a job does wrong is recorded
            state, message = "failed", _said(error)
    await _finish(engine, run, state, message, retries)
    return state


async def _finish(
    engine: AsyncEngine, run: Any, state: str, message: str | None, retries: int
) -> None:
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "UPDATE sys.job_run SET state = :state, finished = now(), message = :message"
                    " WHERE sys_id = :id"
                ),
                {
                    "state": state,
                    "message": (message or None) and message[:MAX_MESSAGE],
                    "id": run.sys_id,
                },
            )
            if run.schedule:
                await conn.execute(
                    text("UPDATE sys.schedule SET last_state = :state WHERE name = :s"),
                    {"state": state, "s": run.schedule},
                )
            if state == "failed" and run.attempt <= retries:
                wait = FIRST_WAIT * (2 ** (run.attempt - 1))
                await conn.execute(
                    text(
                        "INSERT INTO sys.job_run (job, schedule, state, trigger, attempt,"
                        " not_before) VALUES (:job, :schedule, 'queued', 'retry', :attempt,"
                        " now() + :wait)"
                    ),
                    {
                        "job": run.job,
                        "schedule": run.schedule,
                        "attempt": run.attempt + 1,
                        "wait": wait,
                    },
                )


async def recover(engine: AsyncEngine, now: datetime | None = None) -> int:
    """Runs a worker left behind (it stopped mid-run) end as failed; returns how many."""
    now = now or datetime.now(UTC)
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            changed = await conn.execute(
                text(
                    "UPDATE sys.job_run r SET state = 'failed', finished = :now,"
                    " message = 'The worker stopped during the run.' FROM sys.job j"
                    " WHERE j.name = r.job AND r.state = 'running' AND r.deleted IS NULL"
                    " AND r.started < CAST(:now AS timestamptz)"
                    " - make_interval(secs => 2 * j.timeout + 60)"
                ),
                {"now": now},
            )
            return changed.rowcount
