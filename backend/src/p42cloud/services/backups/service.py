"""Making a backup: `pg_dump` of the whole database into the backup folder, then old ones go.

The dump is PostgreSQL's custom format (`pg_restore` reads it). It's written under a temporary
name and renamed once complete, so a backup folder never holds half a dump under a real name.
The password reaches `pg_dump` in its environment, never on its command line.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

PG_DUMP = "pg_dump"
PREFIX = "p42-"
BEFORE_UPDATE = "p42-before-update-"  # deploy/update.sh makes these; the clean-up keeps them
SUFFIX = ".dump"
STAMP = "%Y%m%d-%H%M%S"
LIMIT = 3300.0  # seconds a dump may take: within the job's hour, so it can clean up


class BackupFailed(Exception):
    """The dump didn't work; what pg_dump said."""


@dataclass(frozen=True)
class Found:
    """A backup in the folder."""

    file: str
    made: datetime
    size: int
    kind: str  # daily, before_update


@dataclass(frozen=True)
class Made:
    file: Path
    size: int
    removed: int


def _connection(database_url: str) -> tuple[list[str], dict[str, str]]:
    """pg_dump's arguments for the database, and its environment (with the password)."""
    url = make_url(database_url)
    args = ["--host", url.host or "localhost", "--port", str(url.port or 5432)]
    args += ["--username", url.username or "", "--dbname", url.database or ""]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    if url.password:
        environment["PGPASSWORD"] = str(url.password)
    return args, environment


def _when(file: Path) -> datetime | None:
    try:
        return datetime.strptime(file.name[len(PREFIX) : -len(SUFFIX)], STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def found(folder: Path) -> list[Found]:
    """The backups in the folder, newest first: the daily ones and those made before updates."""
    backups: list[Found] = []
    for file in folder.glob(f"{PREFIX}*{SUFFIX}"):
        if not file.is_file():
            continue
        kind = "before_update" if file.name.startswith(BEFORE_UPDATE) else "daily"
        info = file.stat()
        made = _when(file) if kind == "daily" else None
        made = made or datetime.fromtimestamp(info.st_mtime, UTC)
        backups.append(Found(file.name, made, info.st_size, kind))
    return sorted(backups, key=lambda b: b.made, reverse=True)


def clean_up(folder: Path, keep_days: int, now: datetime) -> int:
    """Removes backups older than the days kept (only files this job named); how many."""
    removed = 0
    for file in folder.glob(f"{PREFIX}*{SUFFIX}"):
        made = _when(file)
        if made is not None and now - made > timedelta(days=keep_days):
            file.unlink()
            removed += 1
    return removed


async def back_up(
    folder: Path, database_url: str, keep_days: int, now: datetime, pg_dump: str = PG_DUMP
) -> Made:
    """Dumps the database into the folder; the file, its size, and how many old ones went."""
    if not folder.is_dir():
        raise BackupFailed(f"The backup folder {folder} isn't there.")
    target = folder / f"{PREFIX}{now:{STAMP}}{SUFFIX}"
    partial = folder / f".{target.name}.partial"
    args, environment = _connection(database_url)
    try:
        process = await asyncio.create_subprocess_exec(
            pg_dump,
            "--format=custom",
            "--no-password",
            "--file",
            str(partial),
            *args,
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise BackupFailed("pg_dump isn't installed where the worker runs.") from error
    try:
        _, said = await asyncio.wait_for(process.communicate(), LIMIT)
    except TimeoutError:
        process.kill()
        partial.unlink(missing_ok=True)
        raise BackupFailed(f"The dump took longer than {LIMIT:.0f} seconds.") from None
    if process.returncode != 0 or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise BackupFailed(said.decode(errors="replace").strip()[:500] or "pg_dump failed.")
    partial.chmod(0o600)
    partial.rename(target)
    return Made(target, target.stat().st_size, clean_up(folder, keep_days, now))
