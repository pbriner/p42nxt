"""Bringing an installation's database to this version: the engine's steps, then housekeeping.

Runs as the owner role (`p42cloud upgrade`), once after setting up and after every update.
Each step runs once, in its own transaction, and is recorded; running it again changes nothing.
"""

from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.engine_steps import STEPS
from p42cloud.kernel.db import owner_transaction, run_script

# Audit partitions are made this many months ahead, so writing never waits for a new month.
MONTHS_AHEAD = 3


def add_months(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


async def upgrade(engine: AsyncEngine, *, today: date | None = None) -> list[str]:
    """Apply the steps not applied yet; returns their names."""
    applied: list[str] = []
    async with owner_transaction(engine) as conn:
        await conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS sys.engine_step"
                " (id text PRIMARY KEY, applied timestamptz NOT NULL DEFAULT now())"
            )
        )
        done = set((await conn.execute(text("SELECT id FROM sys.engine_step"))).scalars())
    for step_id, sql in STEPS:
        if step_id in done:
            continue
        async with owner_transaction(engine) as conn:
            await run_script(conn, sql)
            await conn.execute(
                text("INSERT INTO sys.engine_step (id) VALUES (:id)"), {"id": step_id}
            )
        applied.append(step_id)
    month = (today or datetime.now(UTC).date()).replace(day=1)
    async with owner_transaction(engine) as conn:
        await conn.execute(
            text("SELECT sys.p42_create_audit_partitions(:first, :last)"),
            {"first": month, "last": add_months(month, MONTHS_AHEAD)},
        )
    return applied
