"""What backups add to the core: the job, its schedule, and how long backups are kept."""

from typing import Any

from p42cloud.data.settings import Setting, value
from p42cloud.data.types import Invalid
from p42cloud.kernel.config import get_settings
from p42cloud.services.backups.service import BackupFailed, back_up
from p42cloud.services.jobs.models import Job, JobRun, Schedule

MAX_DAYS = 3650


def _days(given: Any) -> int:
    if isinstance(given, bool) or not isinstance(given, int) or not 1 <= given <= MAX_DAYS:
        raise Invalid(f"From 1 to {MAX_DAYS} days.")
    return given


KEEP_DAYS = Setting(
    "sys.backup_days",
    "Keep backups (days)",
    30,
    check=_days,
    help="Backups older than this are removed after each new one.",
)


async def _back_up(run: JobRun) -> str:
    settings = get_settings()
    if settings.backup_dir is None:
        if settings.environment == "production":
            raise BackupFailed("No backup folder is set (P42_BACKUP_DIR): nothing is backed up.")
        return "No backup folder is set (P42_BACKUP_DIR); not production, so nothing to do."
    keep = int(await value(run.records.conn, KEEP_DAYS.name))
    made = await back_up(settings.backup_dir, settings.owner_database_url, keep, run.now)
    size = made.size / 1024 / 1024
    gone = f"; {made.removed} older one(s) removed" if made.removed else ""
    return f"Backed up: {made.file.name} ({size:.1f} MB){gone}."


JOBS = (Job("backup", "Back up the database", _back_up, timeout=3600, retries=1),)
SCHEDULES = (Schedule("backup", "Database backup, daily", "backup", "30 2 * * *"),)
SETTINGS = (KEEP_DAYS,)
