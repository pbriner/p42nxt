"""What notifications add to the core: their tables, the core's own kinds and the cleanup job."""

from p42cloud.services.jobs.models import Job, JobRun, Schedule
from p42cloud.services.notifications import models
from p42cloud.services.notifications.models import NotificationKind
from p42cloud.services.notifications.service import clean_up

TABLES = models.TABLES
KINDS = (
    NotificationKind(
        "job_failed",
        "A scheduled job fails",
        "A schedule's job failed after its last try, having worked before.",
    ),
)


async def _clean_up(run: JobRun) -> str:
    return f"{await clean_up(run.records.conn)} old notifications put away."


JOBS = (Job("notification_cleanup", "Put away old notifications", _clean_up, timeout=600),)
SCHEDULES = (
    Schedule(
        "notification_cleanup",
        "Old notifications, daily",
        "notification_cleanup",
        "40 3 * * *",
    ),
)
