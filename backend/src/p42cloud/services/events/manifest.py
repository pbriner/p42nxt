"""What events add to the core: their tables, the core's own event types, and putting old
events away."""

from p42cloud.services.events import models
from p42cloud.services.events.models import EventType
from p42cloud.services.events.service import clean_up
from p42cloud.services.jobs.models import Job, JobRun, Schedule

TABLES = models.TABLES
EVENTS = (
    EventType(
        "job_failed",
        "A scheduled job fails",
        about="sys.schedule",
        description="A schedule's job failed after its last try.",
    ),
    EventType(
        "comment_added",
        "A comment is added",
        description="Someone commented on a record (about the record; data: the comment's ID).",
    ),
    EventType(
        "job_recovered",
        "A scheduled job works again",
        about="sys.schedule",
        description="A schedule's job worked after having failed.",
    ),
)


async def _clean_up(run: JobRun) -> str:
    return f"{await clean_up(run.records.conn)} old events put away."


JOBS = (Job("event_cleanup", "Put away old events", _clean_up, timeout=600),)
SCHEDULES = (Schedule("event_cleanup", "Old events, daily", "event_cleanup", "50 3 * * *"),)
