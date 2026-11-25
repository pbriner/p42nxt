"""What approvals add to the core: their tables, the hold on records while they wait, their
notification kinds and the daily follow-up (reminders, escalation)."""

from p42cloud.services.approvals import models, service
from p42cloud.services.jobs.models import Job, JobRun, Schedule
from p42cloud.services.notifications.models import NotificationKind

TABLES = models.TABLES
HOOKS = service.TABLE_HOOKS
KINDS = (
    NotificationKind(
        "approval_asked",
        "Your approval is asked",
        "Something waits for your approval: as an approver, a deputy, or because it waited too"
        " long elsewhere.",
    ),
    NotificationKind(
        "approval_decided",
        "Your request is decided",
        "What you asked approval for was approved or rejected (with the reason).",
    ),
)


async def _follow_up(run: JobRun) -> str:
    reminded, escalated = await service.follow_up(run.records.conn, run.now)
    return f"{reminded} approval(s) reminded, {escalated} escalated."


JOBS = (
    Job("approval_follow_up", "Remind and escalate waiting approvals", _follow_up, timeout=300),
)
SCHEDULES = (
    Schedule("approval_follow_up", "Waiting approvals, daily", "approval_follow_up", "5 7 * * *"),
)
