"""Data for the upgrade check (upgrade-check.sh): `seed` writes it with an older release's code,
`verify` reads it back after upgrading with this one. Uses only what every release has, so the
older code can run it too. Connection settings come from the environment (P42_…)."""

import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any

from sqlalchemy import text

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.config import get_settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction

CRON = "41 4 * * *"  # an administrator's own time for a core schedule: an upgrade keeps it


async def _records(work: Any) -> Any:
    engine = create_engine(get_settings().database_url)
    try:
        with working_as(Context(channel="upgrade-check", access=EVERYTHING)):
            async with transaction(engine, "sys") as conn:
                return await work(Records(conn, await load_catalog(conn)))
    finally:
        await engine.dispose()


async def seed(records: Records) -> dict[str, str]:
    # First, while the transaction still works as the core.
    await records.conn.execute(
        text("UPDATE sys.schedule SET cron = :c WHERE name = 'sys.repair_groups'"), {"c": CRON}
    )
    person = await records.create("sys.person", {"name": "Upgrade Owner"})
    group = await records.create("sys.group", {"name": "Upgrade group"})
    await records.create("sys.membership", {"group": group.sys_id, "person": person.sys_id})
    plan = await records.create(
        "dca.plan", {"name": "Upgrade plan", "owner": person.sys_id, "amount": "50"}
    )
    greeting = await records.create("hello.greeting", {"text": "Still here"})
    return {
        "person": str(person.sys_id),
        "group": str(group.sys_id),
        "plan": str(plan.sys_id),
        "greeting": str(greeting.sys_id),
    }


async def verify(records: Records, seeded: dict[str, str]) -> list[str]:
    found: list[str] = []
    # First what's read with SQL, while the transaction still works as the core.
    conn = records.conn
    cron = await conn.scalar(text("SELECT cron FROM sys.schedule WHERE name = 'sys.repair_groups'"))
    if cron != CRON:
        found.append(f"the schedule's time went back to {cron!r}")
    members = await conn.scalar(
        text(
            'SELECT count(*) FROM sys.membership WHERE "group" = CAST(:g AS uuid)'
            " AND person = CAST(:p AS uuid) AND deleted IS NULL"
        ),
        {"g": seeded["group"], "p": seeded["person"]},
    )
    if members != 1:
        found.append("the membership is gone")
    rows = await conn.execute(
        text("SELECT name, version, publisher FROM sys.module WHERE deleted IS NULL")
    )
    modules = {r.name: (r.version, r.publisher) for r in rows}
    wanted = {
        "sys": version("p42cloud"),
        "dca": version("p42cloud-dca"),
        "hello": version("p42cloud-hello"),
    }
    for module, release in wanted.items():
        if modules.get(module) != (release, "p42"):
            found.append(f"{module} is {modules.get(module)}, not ({release}, 'p42')")
    plan = (await records.read("dca.plan", seeded["plan"])).values  # type: ignore[arg-type]
    if plan["name"] != "Upgrade plan" or str(plan["owner"]) != seeded["person"]:
        found.append(f"the plan changed: {plan}")
    greeting = (await records.read("hello.greeting", seeded["greeting"])).values  # type: ignore[arg-type]
    if greeting["text"] != "Still here":
        found.append(f"the greeting changed: {greeting}")
    return found


def main() -> int:
    command, file = sys.argv[1], Path(sys.argv[2])
    if command == "seed":
        seeded = asyncio.run(_records(seed))
        file.write_text(json.dumps(seeded))
        print(f"Seeded with p42cloud {version('p42cloud')}.")
        return 0
    seeded = json.loads(file.read_text())
    problems = asyncio.run(_records(lambda records: verify(records, seeded)))
    for problem in problems:
        print(f"- {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"The data came through the upgrade to p42cloud {version('p42cloud')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
