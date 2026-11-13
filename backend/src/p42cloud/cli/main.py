"""The p42cloud command line.

Only the operator commands that must run before the app exists work on the database directly
(docs/architecture.md, principle 2); everything else will go through the API.
"""

import argparse
import asyncio
import os
import sys

from p42cloud.kernel.config import get_settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.safety import problems
from p42cloud.kernel.setup import set_up_database


async def _set_up() -> int:
    admin_url = os.environ.get("P42_ADMIN_DATABASE_URL")
    if not admin_url:
        print("Set P42_ADMIN_DATABASE_URL to an administrator connection.", file=sys.stderr)
        return 2
    await set_up_database(admin_url, get_settings())
    print("Database set up.")
    return 0


async def _check() -> int:
    engine = create_engine(get_settings().database_url)
    try:
        found = await problems(engine)
    finally:
        await engine.dispose()
    for problem in found:
        print(f"- {problem}", file=sys.stderr)
    if found:
        print("The database is not safe to use.", file=sys.stderr)
        return 1
    print("The database is set up safely.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p42cloud")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "setup-database", help="create the roles and the core's schema (administrator, once)"
    )
    commands.add_parser("check", help="refuse a database that isn't set up safely")
    args = parser.parse_args(argv)
    match args.command:
        case "setup-database":
            return asyncio.run(_set_up())
        case _:
            return asyncio.run(_check())


if __name__ == "__main__":
    sys.exit(main())
