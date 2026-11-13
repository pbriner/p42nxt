"""The p42cloud command line.

Only the operator commands that must run before the app exists work on the database directly
(docs/architecture.md, principle 2); everything else will go through the API.
"""

import argparse
import asyncio
import os
import sys

from p42cloud.data.audit import Anchor, missing_months, verify_chain
from p42cloud.kernel.config import get_settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.safety import problems
from p42cloud.kernel.setup import set_up_database
from p42cloud.modules.core import install_core


async def _set_up() -> int:
    admin_url = os.environ.get("P42_ADMIN_DATABASE_URL")
    if not admin_url:
        print("Set P42_ADMIN_DATABASE_URL to an administrator connection.", file=sys.stderr)
        return 2
    await set_up_database(admin_url, get_settings())
    print("Database set up.")
    return 0


async def _upgrade() -> int:
    engine = create_engine(get_settings().owner_database_url)
    try:
        with working_as(Context(channel="upgrade")):
            applied = await install_core(engine)
    finally:
        await engine.dispose()
    print(f"Upgraded: {', '.join(applied)}." if applied else "Already up to date.")
    return 0


async def _check() -> int:
    engine = create_engine(get_settings().database_url)
    try:
        found = await problems(engine)
        if not found:
            with working_as(Context(channel="check")):
                async with transaction(engine, "sys") as conn:
                    months = await missing_months(conn)
            if months:
                found.append(
                    f"The audit log has no room yet for {', '.join(months)};"
                    " run `p42cloud upgrade` before then, or changes can't be saved."
                )
    finally:
        await engine.dispose()
    for problem in found:
        print(f"- {problem}", file=sys.stderr)
    if found:
        print("The database is not safe to use.", file=sys.stderr)
        return 1
    print("The database is set up safely.")
    return 0


async def _verify(anchor: Anchor | None) -> int:
    engine = create_engine(get_settings().database_url)
    try:
        with working_as(Context(channel="audit-verify")):
            async with transaction(engine, "sys") as conn:
                result = await verify_chain(conn, anchor)
    finally:
        await engine.dispose()
    if result.intact:
        print(f"The audit log is intact: {result.entries} entries checked.")
        print(f"Its head is {result.head}. Keep a copy outside and pass it with --anchor later.")
        return 0
    print(f"The audit log was tampered with: {result.problem}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p42cloud")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "setup-database", help="create the roles and the core's schema (administrator, once)"
    )
    commands.add_parser("upgrade", help="bring the database to this version (owner)")
    commands.add_parser("check", help="refuse a database that isn't set up safely")
    audit = commands.add_parser("audit", help="the audit log")
    verify = audit.add_subparsers(dest="audit_command", required=True).add_parser(
        "verify", help="recompute the hash chain and report tampering"
    )
    verify.add_argument(
        "--anchor",
        type=Anchor.parse,
        help="an entry's <number>:<hash> noted earlier; finds a chain rewritten as a whole",
    )
    args = parser.parse_args(argv)
    match args.command:
        case "setup-database":
            return asyncio.run(_set_up())
        case "upgrade":
            return asyncio.run(_upgrade())
        case "audit":
            return asyncio.run(_verify(args.anchor))
        case _:
            return asyncio.run(_check())


if __name__ == "__main__":
    sys.exit(main())
