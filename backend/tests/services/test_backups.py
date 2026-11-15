"""Backups: dumped whole into the folder under a dated name, old ones removed, failures told."""

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import create_engine
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
async def engine(upgraded: Settings) -> AsyncEngine:
    return create_engine(upgraded.database_url)


async def test_without_a_folder_the_job_says_so(
    engine: AsyncEngine, upgraded: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As the worker has them: from its environment.
    monkeypatch.delenv("P42_BACKUP_DIR", raising=False)
    monkeypatch.setenv("P42_ENVIRONMENT", "test")
    monkeypatch.setenv("P42_DATABASE_URL", upgraded.database_url)
    monkeypatch.setenv("P42_OWNER_DATABASE_URL", upgraded.owner_database_url)
    state, said = await run_job(engine, "sys.backup")
    await engine.dispose()
    assert state == "succeeded", said
    assert said is not None and said.startswith("No backup folder is set")
