"""What notifications add to the core: their tables, the core's own kinds and the cleanup job."""

from p42cloud.services.jobs.models import Job, JobRun, Schedule
from p42cloud.services.notifications import models, telegram
from p42cloud.services.notifications.models import NotificationKind
from p42cloud.services.notifications.service import clean_up

TABLES = models.TABLES
HOOKS = telegram.TABLE_HOOKS
CONNECTION_TYPES = (telegram.BOT,)
KINDS = (
    NotificationKind(
        "job_failed",
        "A scheduled job fails",
        "A schedule's job failed after its last try, having worked before.",
        channels=("telegram",),
    ),
    NotificationKind(
        "event_failed",
        "An event isn't handled",
        "A module couldn't handle an event after its last try: what it should have done"
        " hasn't happened.",
    ),
    NotificationKind(
        "update_finished",
        "An update is done",
        "An update requested in the app has run: done, or failed (with the version before put"
        " back or not).",
    ),
    NotificationKind(
        "message",
        "A message from administrators",
        "A message administrators send as a notification, to everyone or to a group.",
    ),
    NotificationKind(
        "new_module",
        "A module to install",
        "A module is offered in the registry that this installation hasn't installed yet.",
    ),
    NotificationKind(
        "secret_expiring",
        "A connection's secret expires",
        "Two weeks before a connection's secret expires, and on the day: time to get a new one.",
    ),
    NotificationKind(
        "module_roles",
        "A module brings roles",
        "After a module is installed: the roles it brings, to give to groups of people.",
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
