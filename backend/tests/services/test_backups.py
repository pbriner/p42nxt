"""Backups: dumped whole into the folder under a dated name, old ones removed, failures told."""

import json
import stat
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.types import Invalid
from p42cloud.kernel.config import Settings, get_settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.services.backups import manifest
from p42cloud.services.backups.service import BackupFailed, back_up, clean_up
from p42cloud.services.jobs.service import run_job

URL = "postgresql+asyncpg://p42_owner:s3cret-pass@db.example:5433/p42"
NOW = datetime(2026, 11, 14, 2, 30, tzinfo=UTC)


def _fake_pg_dump(folder: Path, *, fails: bool = False) -> str:
    """A stand-in for pg_dump: writes what it was given (arguments, password) as the dump."""
    script = folder / "pg_dump"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        + ("sys.stderr.write('connection refused'); sys.exit(1)\n" if fails else "")
        + "out = sys.argv[sys.argv.index('--file') + 1]\n"
        "open(out, 'w').write(json.dumps({'args': sys.argv[1:],"
        " 'password': os.environ.get('PGPASSWORD')}))\n"
    )
    script.chmod(0o755)
    return str(script)


async def test_a_backup_is_a_whole_private_dump_named_by_its_time(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    made = await back_up(folder, URL, 30, NOW, pg_dump=_fake_pg_dump(tmp_path))
    written = json.loads(made.file.read_text())
    assert made.file.name == "p42-20261114-023000.dump"
    assert [p.name for p in folder.iterdir()] == [made.file.name]  # no partial file left
    assert stat.S_IMODE(made.file.stat().st_mode) == 0o600
    assert written["password"] == "s3cret-pass"
    assert "s3cret-pass" not in " ".join(written["args"])  # never on the command line
    assert {"--format=custom", "--host", "db.example", "--port", "5433"} <= set(written["args"])
    assert written["args"][written["args"].index("--dbname") + 1] == "p42"


async def test_a_failed_dump_leaves_nothing_and_says_why(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    with pytest.raises(BackupFailed, match="connection refused"):
        await back_up(folder, URL, 30, NOW, pg_dump=_fake_pg_dump(tmp_path, fails=True))
    with pytest.raises(BackupFailed, match="isn't installed"):
        await back_up(folder, URL, 30, NOW, pg_dump=str(tmp_path / "nowhere"))
    with pytest.raises(BackupFailed, match="isn't there"):
        await back_up(tmp_path / "missing", URL, 30, NOW, pg_dump=_fake_pg_dump(tmp_path))
    assert list(folder.iterdir()) == []


def test_old_backups_go_and_other_files_stay(tmp_path: Path) -> None:
    for days in (1, 29, 31, 400):
        (tmp_path / f"p42-{NOW - timedelta(days=days):%Y%m%d-%H%M%S}.dump").write_text("x")
    (tmp_path / "p42-notes.dump").write_text("mine")
    (tmp_path / "other.dump").write_text("mine")
    assert clean_up(tmp_path, 30, NOW) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "other.dump",
        "p42-20261016-023000.dump",
        "p42-20261113-023000.dump",
        "p42-notes.dump",
    ]


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def worker(upgraded: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """The worker's environment: its database, and a backup folder given to it (or not)."""
    monkeypatch.setenv("P42_ENVIRONMENT", "test")
    monkeypatch.setenv("P42_DATABASE_URL", upgraded.database_url)
    monkeypatch.setenv("P42_OWNER_DATABASE_URL", upgraded.owner_database_url)
    monkeypatch.delenv("P42_BACKUPS_MOUNTED", raising=False)
    mount = tmp_path / "mount"
    monkeypatch.setattr(manifest, "MOUNT", mount)
    fake = _fake_pg_dump(tmp_path)

    async def with_fake(*args: Any, **kwargs: Any) -> Any:
        return await back_up(*args, **kwargs, pg_dump=fake)

    monkeypatch.setattr(manifest, "back_up", with_fake)
    get_settings.cache_clear()  # read from this environment
    yield mount
    get_settings.cache_clear()


async def _set_folder(upgraded: Settings, folder: str) -> None:
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.execute(
                text("UPDATE sys.setting SET value = CAST(:v AS jsonb) WHERE name = :n"),
                {"v": json.dumps(folder), "n": "sys.backup_folder"},
            )
    await owner.dispose()


async def _backups(upgraded: Settings) -> list[tuple[str, str, str]]:
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT file, folder, kind FROM sys.backup WHERE deleted IS NULL"
                        " ORDER BY file"
                    )
                )
            ).all()
    await owner.dispose()
    return [(r.file, r.folder, r.kind) for r in rows]


async def test_without_a_folder_the_job_says_so(engine: AsyncEngine, worker: Path) -> None:
    state, said = await run_job(engine, "sys.backup")
    assert state == "succeeded", said
    assert said is not None and said.startswith("No backup folder is set")


async def test_the_backups_in_the_folder_become_records(
    engine: AsyncEngine, worker: Path, upgraded: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.mkdir()
    (worker / "p42-before-update-20261101-120000.dump").write_text("x")
    (worker / "notes.txt").write_text("not a backup")
    await _set_folder(upgraded, "/srv/backups/p42")
    monkeypatch.setenv("P42_BACKUPS_MOUNTED", "/srv/backups/p42")
    get_settings.cache_clear()
    state, said = await run_job(engine, "sys.backup")
    assert state == "succeeded", said
    assert said is not None and "in /srv/backups/p42." in said
    found = await _backups(upgraded)
    assert sorted(k for _, _, k in found) == ["before_update", "daily"]
    assert {f for _, f, _ in found} == {"/srv/backups/p42"}

    # Changed in the app, not applied on the machine yet: backed up where it can, and said.
    await _set_folder(upgraded, "/srv/elsewhere")
    (worker / "p42-before-update-20261101-120000.dump").unlink()
    state, said = await run_job(engine, "sys.backup")
    assert state == "succeeded", said
    assert said is not None and "changed to /srv/elsewhere" in said
    assert {k for _, _, k in await _backups(upgraded)} == {"daily"}  # the gone one retired
    await _set_folder(upgraded, "")


async def test_a_folder_set_but_not_given_to_the_worker_fails(
    engine: AsyncEngine, worker: Path, upgraded: Settings
) -> None:
    await _set_folder(upgraded, "/srv/backups/p42")
    state, said = await run_job(engine, "sys.backup")
    await _set_folder(upgraded, "")
    assert state == "failed"
    assert said is not None and "isn't given to the worker" in said


@pytest.mark.parametrize(
    ("given", "problem"),
    [
        ("Backups", "starting with /"),
        ("/Users/anna/../etc", "starting with /"),
        ("/Users/anna/Back$ups", "starting with /"),
        ("/srv/a:/b", "starting with /"),
        ("/", "own folders"),
        ("/Users", "own folders"),
        ("/etc/p42", "own folders"),
        ("/tmp/p42", "temporary"),  # noqa: S108 - refused, not used
        ("/private/var/folders/x/p42", "temporary"),
    ],
)
def test_the_backup_folder_must_be_a_folder_of_its_own(given: str, problem: str) -> None:
    with pytest.raises(Invalid, match=problem):
        manifest.FOLDER.check(given)


def test_a_good_backup_folder_is_kept_as_written() -> None:
    assert manifest.FOLDER.check("/Users/anna/Backups/p42cloud prod/") == (
        "/Users/anna/Backups/p42cloud prod"
    )
    assert manifest.FOLDER.check("") == ""
