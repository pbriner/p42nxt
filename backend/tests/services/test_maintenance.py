"""The core's maintenance jobs: the audit log's months ahead, and its chain checked."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.audit import Anchor, ChainCheck
from p42cloud.data.upgrade import add_months
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.services.jobs.service import run_job
from p42cloud.services.maintenance import manifest


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def _partitions(engine: AsyncEngine) -> set[str]:
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            found = await conn.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
                    " WHERE i.inhparent = 'sys.audit_log'::regclass"
                )
            )
            return set(found.scalars())


async def test_the_audit_logs_coming_months_are_prepared(engine: AsyncEngine) -> None:
    state, said = await run_job(engine, "sys.audit_months")
    assert state == "succeeded", said
    this = datetime.now(UTC).date().replace(day=1)
    wanted = {f"audit_log_{add_months(this, n):%Y_%m}" for n in range(manifest.MONTHS_AHEAD + 1)}
    assert wanted <= await _partitions(engine)
    again, _ = await run_job(engine, "sys.audit_months")  # nothing left to do: fine
    assert again == "succeeded"


async def test_the_months_ahead_are_bounded(engine: AsyncEngine) -> None:
    with (
        pytest.raises(DBAPIError, match="0 to 12 months"),
        working_as(Context(channel="test", access=EVERYTHING)),
    ):
        async with transaction(engine, "sys") as conn:
            await conn.execute(text("SELECT sys.p42_audit_months_ahead(500)"))


async def test_the_audit_log_is_checked(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, said = await run_job(engine, "sys.audit_check")
    assert state == "succeeded"
    assert said is not None and said.startswith("The audit log is intact:")

    async def broken(conn: object, anchor: object = None) -> ChainCheck:
        return ChainCheck(3, Anchor(3, b"\x00"), "Entries 2 to 3 are missing.")

    monkeypatch.setattr(manifest, "verify_chain", broken)
    state, said = await run_job(engine, "sys.audit_check")
    assert state == "failed"
    assert said is not None and "tampered with: Entries 2 to 3 are missing." in said
