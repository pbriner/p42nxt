"""A real installation for the browser tests: a fresh database with the core and the modules,
some people, and the app serving the built frontend. The database goes when it stops.

    uv run --directory ../backend python ../frontend/e2e/installation.py --port 8899

Writes what the tests need (addresses, people, their authenticator keys) to
e2e/.state-<port>.json. Needs the development PostgreSQL (docker compose up -d).

Its database is dropped when it stops. One that a killed run left behind is dropped by the next
run, once nothing is connected to it any more.
"""

import argparse
import asyncio
import base64
import json
import os
import signal
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.secrets import Sealer
from p42cloud.kernel.setup import set_up_database
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.registry import discover
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.sign_in import credentials, totp
from p42cloud.services.notifications.service import send
from p42cloud.web.app import create_app
from p42cloud.worker.main import work

ADMIN_URL = os.environ.get(
    "P42_TEST_ADMIN_URL", "postgresql+asyncpg://p42_admin:p42-admin-dev-only@localhost:5435/p42"
)
HOST = ADMIN_URL.rsplit("@", 1)[1].rsplit("/", 1)[0]
PASSWORD = "correct horse battery staple"  # noqa: S105 - test people in a throwaway database
# One person per browser size signs in through the screens; the others sign in once, by API.
SIGNERS = ("desktop", "dark", "laptop", "tablet", "phone")
HERE = Path(__file__).resolve().parent
SETUP_CODE = "E2ESETUP"


async def _person(
    records: Records, sealer: Sealer, name: str, email: str, groups: list[uuid.UUID]
) -> dict[str, str]:
    person = await records.create("sys.person", {"name": name, "email": email})
    for group in groups:
        await records.create("sys.membership", {"group": group, "person": person.sys_id})
    await credentials.set_password(records.conn, person.sys_id, PASSWORD)
    made = await credentials.start_totp(records.conn, sealer, person.sys_id, "Phone")
    key = base64.b32decode(made.key + "=" * (-len(made.key) % 8))
    # Confirmed with the step before now, so the tests' first code is still unused.
    await credentials.confirm_totp(
        records.conn,
        sealer,
        person.sys_id,
        made.credential,
        totp.code(key, totp.step_of(datetime.now(UTC)) - 1),
    )
    return {"name": name, "email": email, "password": PASSWORD, "key": made.key}


async def _demo(records: Records, admin: dict[str, str]) -> None:
    """For trying by hand: the admin sets up their own authenticator, and there's something
    to look at."""
    await records.conn.execute(
        text(
            "UPDATE sys.credential SET deleted = now() WHERE kind <> 'password'"
            " AND person = (SELECT sys_id FROM sys.person WHERE email = :e)"
        ),
        {"e": admin["email"]},
    )
    admin["key"] = ""
    plain = await records.conn.scalar(
        text("SELECT sys_id FROM sys.person WHERE email = 'plain@e2e.test'")
    )
    for words, language in (
        ("Hello from Zurich!", "en"),
        ("Grüezi mitenand", "de"),
        ("Bonjour à tous", "fr"),
        ("Good morning, team", "en"),
        ("Schönes Wochenende", "de"),
    ):
        await records.create(
            "hello.greeting", {"text": words, "language": language, "author": plain}
        )


async def _notifications(records: Records, people: dict[str, dict[str, str]]) -> None:
    """Something in the bell: one for the admin per browser size, a few for Pat."""
    ids = {
        name: await records.conn.scalar(
            text("SELECT sys_id FROM sys.person WHERE email = :e"), {"e": who["email"]}
        )
        for name, who in people.items()
        if name in ("admin", "plain")
    }
    for size in ("phone", "tablet", "laptop", "dark", "desktop"):
        await send(
            records.conn,
            "sys.job_failed",
            f"Failed: Nightly import ({size})",
            people=[ids["admin"]],
            message="The outside system didn't answer in time.",
            link="/list/sys.job_run",
            severity="error",
        )
    for title, severity in (
        ("Your export is ready", "success"),
        ("Storage is almost full", "warning"),
        ("Welcome to P42cloud", "info"),
    ):
        await send(records.conn, "sys.job_failed", title, people=[ids["plain"]], severity=severity)


async def _prepare(
    database: str, settings: Settings, sealer: Sealer, *, demo: bool, fresh: bool
) -> dict[str, object]:
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{database}"'))
    await admin.dispose()
    await set_up_database(ADMIN_URL.rsplit("/", 1)[0] + f"/{database}", settings)
    owner = create_engine(settings.owner_database_url)
    with working_as(Context(channel="upgrade")):
        await install_modules(owner, [CORE, *discover()])
    await owner.dispose()
    if fresh:
        return {"people": {}, "setup_code": settings.setup_code}
    engine = create_engine(settings.database_url)
    with working_as(Context(channel="ui", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            greeters = (await records.create("sys.group", {"name": "Greeters"})).sys_id
            role = await conn.scalar(text("SELECT sys_id FROM sys.role WHERE name = 'hello.user'"))
            await records.create("sys.group_role", {"group": greeters, "role": role})
            people = {
                "admin": await _person(
                    records, sealer, "Ada Admin", "admin@e2e.test", [ADMINISTRATORS, greeters]
                ),
                "plain": await _person(records, sealer, "Pat Plain", "plain@e2e.test", [greeters]),
            }
            for size in SIGNERS:
                people[f"signer-{size}"] = await _person(
                    records, sealer, f"Sam {size.title()}", f"{size}@e2e.test", [greeters]
                )
                # A password only: sets up the second factor on the first sign-in.
                new = await records.create(
                    "sys.person", {"name": f"Noa {size.title()}", "email": f"new-{size}@e2e.test"}
                )
                await credentials.set_password(records.conn, new.sys_id, PASSWORD)
                people[f"newcomer-{size}"] = {
                    "name": f"Noa {size.title()}",
                    "email": f"new-{size}@e2e.test",
                    "password": PASSWORD,
                    "key": "",
                }
            await _notifications(records, people)
            if demo:
                await _demo(records, people["admin"])
    await engine.dispose()
    return {"people": people}


async def _drop_leftovers() -> None:
    """Test databases of runs that were killed: nobody is connected to them any more."""
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        left = await conn.scalars(
            text(
                "SELECT datname FROM pg_database d WHERE datname LIKE 'p42\\_e2e\\_%'"
                " AND NOT EXISTS (SELECT 1 FROM pg_stat_activity a WHERE a.datname = d.datname)"
            )
        )
        for name in list(left):
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    await admin.dispose()


async def _drop(database: str) -> None:
    admin = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
    await admin.dispose()


def _worker(settings: Settings, stopping: threading.Event) -> None:
    async def run() -> None:
        stop = asyncio.Event()

        async def watch() -> None:
            while not stopping.is_set():
                await asyncio.sleep(0.5)
            stop.set()

        watcher = asyncio.create_task(watch())
        await work(stop, settings)
        watcher.cancel()

    asyncio.run(run())


def _stop(_signal: int, _frame: object) -> None:
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="for trying by hand: the admin sets up their own authenticator; some greetings",
    )
    parser.add_argument(
        "--fresh", action="store_true", help="nobody set up yet: for the web installer's tests"
    )
    args = parser.parse_args()
    args.state = args.state or HERE / f".state-{args.port}.json"
    # Stopping (Ctrl-C, or the test runner's SIGTERM) ends the server, so the database is
    # dropped below.
    signal.signal(signal.SIGTERM, _stop)
    database = f"p42_e2e_{uuid.uuid4().hex[:8]}"
    url = f"http://localhost:{args.port}"
    settings = Settings(
        environment="test",
        database_url=f"postgresql+asyncpg://p42_app:p42-app-dev-only@{HOST}/{database}",
        owner_database_url=f"postgresql+asyncpg://p42_owner:p42-owner-dev-only@{HOST}/{database}",
        public_url=url,
        requests_per_minute=100_000,
        setup_code=SETUP_CODE if args.fresh else None,
    )
    sealer = Sealer(os.urandom(32))
    stopping = threading.Event()
    worker: threading.Thread | None = None
    asyncio.run(_drop_leftovers())
    try:
        state = asyncio.run(_prepare(database, settings, sealer, demo=args.demo, fresh=args.fresh))
        args.state.write_text(json.dumps({"url": url, **state}, indent=2))
        # The worker beside the app, as in an installation (its own thread and loop).
        worker = threading.Thread(target=_worker, args=(settings, stopping), daemon=True)
        worker.start()
        uvicorn.run(
            create_app(settings, sealer=sealer, upgrade=False),
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    finally:
        stopping.set()
        if worker is not None:
            worker.join(timeout=10)
        asyncio.run(_drop(database))
        args.state.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
