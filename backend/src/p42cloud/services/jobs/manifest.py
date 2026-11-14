"""What jobs add to the core: their tables, the check on schedules, and the core's own jobs."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from p42cloud.data.hooks import Change, TableHook
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.groups import recalculate_group
from p42cloud.services.jobs import models
from p42cloud.services.jobs.cron import parse
from p42cloud.services.jobs.models import Job, JobRun, Schedule
from p42cloud.services.jobs.service import cron_problems

TABLES = models.TABLES


async def _check_schedule(change: Change) -> None:
    """A schedule's time is checked, and its next run follows a change of when it runs."""
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        raise Problem(409, "declared_only", "Schedules come with their modules.")
    current: Any = (
        await change.records.conn.execute(
            text("SELECT cron, time_zone, active FROM sys.schedule WHERE sys_id = :id"),
            {"id": change.sys_id},
        )
    ).first()
    cron = str(change.values.get("cron", current.cron))
    zone = str(change.values.get("time_zone", current.time_zone))
    found = cron_problems("cron", cron, zone)
    if found:
        raise Problem(422, "validation", "Some fields aren't valid.", errors=found)
    if {"cron", "time_zone", "active"} & set(change.values):
        change.values["next_run"] = parse(cron).next_after(datetime.now(UTC), zone)


async def _repair_groups(run: JobRun) -> str:
    """Dynamic groups' members, from their rules: for rules on time, and repairs."""
    conn = run.records.conn
    groups = list(
        await conn.scalars(
            text(
                'SELECT sys_id FROM sys."group" WHERE rule IS NOT NULL AND provider IS NULL'
                " AND deleted IS NULL"
            )
        )
    )
    for group in groups:
        await recalculate_group(conn, run.records.catalog, group)
    return f"{len(groups)} groups checked."


HOOKS = (TableHook("sys.schedule", before=_check_schedule),)
JOBS = (
    Job(
        "repair_groups", "Check the dynamic groups' members", _repair_groups, timeout=600, retries=1
    ),
)
SCHEDULES = (Schedule("repair_groups", "Dynamic groups, nightly", "repair_groups", "23 2 * * *"),)
