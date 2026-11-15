"""What backups add to the core: the job, its schedule, the backup folder and how long backups
are kept (settings), and the backups as records.

The backup folder is a setting: the folder on the machine. The worker runs in a container and
sees only what it's given, at `/backups`; the deploy scripts give it the folder the setting names
(`deploy/restart.sh`, `deploy/update.sh`) and tell it which one that is. Until they've run after
a change, the job says the change is waiting.
"""

import re
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.settings import Setting, value
from p42cloud.data.types import Invalid
from p42cloud.kernel.config import get_settings
from p42cloud.services.backups.service import BackupFailed, Found, back_up, found
from p42cloud.services.jobs.models import Job, JobRun, Schedule

MAX_DAYS = 3650
MOUNT = Path("/backups")  # where the worker's container has the backup folder
# Letters, digits, spaces and . _ - + @ only: the folder ends up in Docker's configuration.
_SAFE = re.compile(r"^/[A-Za-z0-9 ._+@/-]{0,480}$")
# The machine's own folders: never a place for backups.
_SYSTEM = ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/sbin", "/sys", "/usr", "/System")
_ROOTS = ("/", "/home", "/Users", "/var", "/opt", "/root", "/private", "/Volumes", "/mnt")
# Emptied by the machine: backups there would vanish.
_TEMPORARY = ("/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp", "/private/var/folders")  # noqa: S108


def _days(given: Any) -> int:
    if isinstance(given, bool) or not isinstance(given, int) or not 1 <= given <= MAX_DAYS:
        raise Invalid(f"From 1 to {MAX_DAYS} days.")
    return given


def _under(folder: str, roots: tuple[str, ...]) -> bool:
    return any(folder == r or folder.startswith(f"{r}/") for r in roots)


def _folder(given: Any) -> str:
    if not isinstance(given, str):
        raise Invalid("A folder on the machine, like /Users/you/Backups/p42cloud.")
    folder = given.strip().rstrip("/") or given.strip()
    if folder == "":
        return ""
    path = PurePosixPath(folder)
    if not _SAFE.match(folder) or ".." in path.parts or "//" in folder:
        raise Invalid(
            "A full path starting with /, with letters, digits, spaces and . _ - + @ only."
        )
    if folder in _ROOTS or _under(folder, _SYSTEM):
        raise Invalid("Not one of the machine's own folders: choose a folder of its own.")
    if _under(folder, _TEMPORARY):
        raise Invalid("Not a temporary folder: the machine empties it.")
    return folder


KEEP_DAYS = Setting(
    "sys.backup_days",
    "Keep backups (days)",
    30,
    check=_days,
    help="Daily backups older than this are removed after each new one.",
)
FOLDER = Setting(
    "sys.backup_folder",
    "Backup folder",
    "",
    check=_folder,
    help=(
        "The folder on the machine the daily backups go to. A change applies when the"
        " installation is next restarted or updated on the machine (deploy/restart.sh or"
        " deploy/update.sh). Copy this folder somewhere else too: a backup on the same machine"
        " doesn't survive the machine."
    ),
)


async def _record(conn: AsyncConnection, folder: str, backups: list[Found]) -> None:
    """The folder's backups as records: new ones added, gone ones retired."""
    names = [b.file for b in backups]
    await conn.execute(
        text(
            "UPDATE sys.backup SET deleted = now() WHERE deleted IS NULL"
            " AND (folder <> :folder OR NOT file = ANY(:names))"
        ),
        {"folder": folder, "names": names},
    )
    known = set(
        await conn.scalars(
            text("SELECT file FROM sys.backup WHERE deleted IS NULL AND folder = :folder"),
            {"folder": folder},
        )
    )
    for backup in backups:
        if backup.file in known:
            continue
        await conn.execute(
            text(
                "INSERT INTO sys.backup (file, folder, made, size, kind)"
                " VALUES (:file, :folder, :made, :size, :kind)"
            ),
            {
                "file": backup.file,
                "folder": folder,
                "made": backup.made,
                "size": round(backup.size / 1024 / 1024, 1),
                "kind": backup.kind,
            },
        )


async def _back_up(run: JobRun) -> str:
    settings = get_settings()
    conn = run.records.conn
    wanted = str(await value(conn, FOLDER.name) or "")
    given = settings.backups_mounted
    production = settings.environment == "production"
    if not wanted and given is None:
        if production:
            raise BackupFailed(
                "No backup folder is set (Platform, Settings, Backup folder): nothing is backed up."
            )
        return "No backup folder is set; not production, so nothing to do."
    if given is None or not MOUNT.is_dir():
        raise BackupFailed(
            f"The backup folder {wanted} isn't given to the worker yet: restart the installation"
            " on the machine (deploy/restart.sh)."
        )
    keep = int(await value(conn, KEEP_DAYS.name))
    made = await back_up(MOUNT, settings.owner_database_url, keep, run.now)
    await _record(conn, given, found(MOUNT))
    size = made.size / 1024 / 1024
    said = f"Backed up: {made.file.name} ({size:.1f} MB) in {given}"
    said += f"; {made.removed} older one(s) removed." if made.removed else "."
    if wanted and wanted != given:
        said += f" The backup folder was changed to {wanted}: that applies at the next restart."
    return said


JOBS = (Job("backup", "Back up the database", _back_up, timeout=3600, retries=1),)
SCHEDULES = (Schedule("backup", "Database backup, daily", "backup", "30 2 * * *"),)
SETTINGS = (FOLDER, KEEP_DAYS)
