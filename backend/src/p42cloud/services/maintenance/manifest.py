"""The core's maintenance jobs: the audit log's months prepared ahead, its chain checked, and
connection secrets about to expire told to their owners.

A failed run is like any failed scheduled job: administrators are notified (and `sys.job_failed`
is published), once, until it works again.
"""

from datetime import timedelta

from sqlalchemy import text

from p42cloud.data.audit import missing_months, verify_chain
from p42cloud.kernel.errors import Problem
from p42cloud.services.jobs.models import Job, JobRun, Schedule
from p42cloud.services.notifications.service import send

MONTHS_AHEAD = 3
WARN_BEFORE = timedelta(days=14)
_EXPIRING = text(
    "SELECT s.sys_id, s.name, s.expires, s.owner, s.connection, c.label"
    " FROM sys.connection_secret s JOIN sys.connection c ON c.sys_id = s.connection"
    " WHERE s.deleted IS NULL AND c.deleted IS NULL AND s.expires IS NOT NULL"
    " AND s.expires <= :soon AND (s.told IS NULL OR (s.expires <= :today AND s.told < s.expires))"
)


async def _audit_months(run: JobRun) -> str:
    conn = run.records.conn
    await conn.execute(text("SELECT sys.p42_audit_months_ahead(:n)"), {"n": MONTHS_AHEAD})
    if missing := await missing_months(conn):
        raise Problem(500, "audit_months", f"The audit log has no room for {', '.join(missing)}.")
    return f"The audit log is ready for this month and {MONTHS_AHEAD} ahead."


async def _audit_check(run: JobRun) -> str:
    found = await verify_chain(run.records.conn)
    if found.problem is not None:
        raise Problem(500, "audit_tampered", f"The audit log was tampered with: {found.problem}")
    return (
        f"The audit log is intact: {found.entries} entries checked. Its head is {found.head};"
        " keep a copy outside to check against later."
    )


async def _secrets_expiring(run: JobRun) -> str:
    """Two weeks before a secret expires, and on the day, its owner is told (once each)."""
    conn = run.records.conn
    today = run.now.date()
    rows = (
        await conn.execute(
            _EXPIRING,
            {"soon": today + WARN_BEFORE, "today": today},
        )
    ).all()
    for row in rows:
        gone = row.expires <= today
        title = (
            f"The secret {row.name} of {row.label} has expired"
            if gone
            else f"The secret {row.name} of {row.label} expires on {row.expires.isoformat()}"
        )
        await send(
            conn,
            "sys.secret_expiring",
            title,
            people=(row.owner,),
            message="Get a new one from the outside system and enter it on the connection.",
            link=f"/form/sys.connection/{row.connection}",
            severity="warning" if gone else "info",
        )
        await conn.execute(
            text("UPDATE sys.connection_secret SET told = :today WHERE sys_id = :id"),
            {"today": today, "id": row.sys_id},
        )
    return f"{len(rows)} secret(s) about to expire told to their owners."


JOBS = (
    Job("audit_months", "Prepare the audit log's coming months", _audit_months, timeout=120),
    Job("audit_check", "Check the audit log for tampering", _audit_check, timeout=3600, retries=0),
    Job(
        "secret_expiry", "Remind owners of secrets about to expire", _secrets_expiring, timeout=120
    ),
)
SCHEDULES = (
    Schedule("audit_months", "Audit log months, weekly", "audit_months", "10 1 * * 1"),
    Schedule("audit_check", "Audit log check, weekly", "audit_check", "40 4 * * 0"),
    Schedule("secret_expiry", "Expiring secrets, daily", "secret_expiry", "15 6 * * *"),
)
