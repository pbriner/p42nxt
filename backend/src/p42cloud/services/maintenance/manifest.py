"""The core's maintenance jobs: the audit log's months prepared ahead, and its chain checked.

A failed run is like any failed scheduled job: administrators are notified (and `sys.job_failed`
is published), once, until it works again.
"""

from sqlalchemy import text

from p42cloud.data.audit import missing_months, verify_chain
from p42cloud.kernel.errors import Problem
from p42cloud.services.jobs.models import Job, JobRun, Schedule

MONTHS_AHEAD = 3


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


JOBS = (
    Job("audit_months", "Prepare the audit log's coming months", _audit_months, timeout=120),
    Job("audit_check", "Check the audit log for tampering", _audit_check, timeout=3600, retries=0),
)
SCHEDULES = (
    Schedule("audit_months", "Audit log months, weekly", "audit_months", "10 1 * * 1"),
    Schedule("audit_check", "Audit log check, weekly", "audit_check", "40 4 * * 0"),
)
