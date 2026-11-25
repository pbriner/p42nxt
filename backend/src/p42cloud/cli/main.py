"""The p42cloud command line.

Only the operator commands that must run before the app exists work on the database directly
(docs/architecture.md, principle 2); everything else will go through the API.
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data import installation
from p42cloud.data.audit import Anchor, missing_months, verify_chain
from p42cloud.data.settings import value
from p42cloud.data.types import Invalid
from p42cloud.kernel.config import get_settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.outgoing import safe_client
from p42cloud.kernel.safety import problems
from p42cloud.kernel.secrets import Sealer
from p42cloud.kernel.setup import set_up_database
from p42cloud.modules.core import CORE, compose
from p42cloud.modules.core_release import verified_image
from p42cloud.modules.install import compose as compose_modules
from p42cloud.modules.install import install_modules, states
from p42cloud.modules.install_package import Source, install_package
from p42cloud.modules.lifecycle import remove, switch_off, switch_on
from p42cloud.modules.packages import hosts_for
from p42cloud.modules.registry import discover
from p42cloud.modules.updates import refresh
from p42cloud.security.sign_in.credentials import NewKey
from p42cloud.security.sign_in.first_admin import create_first_admin
from p42cloud.security.sign_in.recovery import operator_token, reset_second_factor
from p42cloud.services.backups.manifest import FOLDER
from p42cloud.services.update_requests import service as update_requests


async def _set_up() -> int:
    admin_url = os.environ.get("P42_ADMIN_DATABASE_URL")
    if not admin_url:
        print("Set P42_ADMIN_DATABASE_URL to an administrator connection.", file=sys.stderr)
        return 2
    await set_up_database(admin_url, get_settings())
    print("Database set up.")
    return 0


async def _upgrade() -> int:
    settings = get_settings()
    engine = create_engine(settings.owner_database_url)
    try:
        with working_as(Context(channel="upgrade")):
            applied = await install_modules(engine, [CORE, *discover(settings.modules_dir)])
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


async def _ask_code(key: NewKey) -> str:
    print("\nAdd this account to your authenticator app (scan the link as a QR code, or type")
    print(f"the key):\n\n  {key.link}\n  Key: {key.key}\n")
    return await asyncio.to_thread(input, "Code from the app: ")


async def _create_admin(name: str, email: str) -> int:
    password = getpass.getpass("Password (12 characters or more): ")
    if getpass.getpass("The same again: ") != password:
        print("The passwords differ; nothing was made.", file=sys.stderr)
        return 1
    settings = get_settings()
    compose()
    engine = create_engine(settings.database_url)
    try:
        codes = await create_first_admin(
            engine,
            Sealer.from_file(settings.master_key_file),
            name=name,
            email=email,
            password=password,
            ask_code=_ask_code,
        )
    except Problem as problem:
        print(f"{problem.title} {' '.join(e.message for e in problem.errors)}", file=sys.stderr)
        return 1
    except RuntimeError as error:  # no master key
        print(error, file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(
        f"\n{name} is an administrator. Keep these recovery codes somewhere safe; each works once"
    )
    print("when the app isn't at hand:\n")
    for code in codes:
        print(f"  {code}")
    return 0


async def _registry(engine: AsyncEngine) -> str | None:
    """The setting Release address (read as the owner), else the environment's."""
    async with owner_transaction(engine) as conn:
        found = await value(conn, installation.RELEASE_ADDRESS.name)
    return str(found or "") or get_settings().registry_url


async def _from_package(engine: AsyncEngine, module: str, source: Source) -> list[str]:
    settings = get_settings()
    registry = await _registry(engine)
    source = Source(registry, source.version, source.file)
    hosts = hosts_for(registry) if registry else ()
    async with safe_client(allowed_hosts=hosts) as http:
        done = await install_package(engine, module, settings.modules_dir, source, http)
    return [*done, "Restart the app and the worker to use it."]


async def _module(action: str, module: str | None, source: Source) -> int:
    settings = get_settings()
    engine = create_engine(settings.owner_database_url)
    try:
        with working_as(Context(channel=f"cli:module:{action}")):
            if action == "updates":
                registry = await _registry(engine)
                async with safe_client(allowed_hosts=hosts_for(registry or "")) as http:
                    found = await refresh(engine, http, registry)
                for update in found:
                    kind = "security update" if update.security else "update"
                    state = "fits" if update.fits else update.needs
                    versions = f"{update.installed} → {update.available}"
                    print(f"{update.module:16} {versions}  {kind}, {state}")
                print("" if found else "Everything is up to date.")
                return 0
            if action == "list":
                for name, state in sorted((await states(engine)).items()):
                    shown = "removed" if state.removed else "on" if state.enabled else "off"
                    print(f"{name:16} {state.version!s:12} {shown}")
                return 0
            if module is None:
                print("Name the module.", file=sys.stderr)
                return 2
            here = {m.id for m in discover(settings.modules_dir)}
            if action == "update" or (action == "install" and (source.file or module not in here)):
                print("\n".join(await _from_package(engine, module, source)))
            elif action == "install":
                done = await install_modules(
                    engine, [CORE, *discover(settings.modules_dir)], bring_back=frozenset({module})
                )
                print("\n".join(done) or "Already up to date.")
            else:
                await {"on": switch_on, "off": switch_off, "remove": remove}[action](engine, module)
                print(f"{module}: {action}.")
    except Problem as problem:
        print(f"{problem.title} {problem.detail or ''}".strip(), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


async def _recover(what: str, email: str, name: str, hours: int) -> int:
    """For the operator: a person's second factor reset, or a short-lived token for them."""
    engine = create_engine(get_settings().database_url)
    try:
        with working_as(Context(channel=f"cli:{what}", access=EVERYTHING)):
            async with transaction(engine, "sys") as conn:
                if what == "reset-second-factor":
                    await reset_second_factor(conn, email)
                    print(
                        f"{email}: second factor reset, sessions ended. They set up a new one at"
                        " their next sign-in, with their password.",
                        file=sys.stderr,
                    )
                else:
                    made = await operator_token(conn, email, name, hours)
                    print(
                        f"Token for {email}, until {made.expires:%Y-%m-%d %H:%M} UTC:",
                        file=sys.stderr,
                    )
                    print(made.token)
    except Problem as problem:
        print(f"{problem.title} {problem.detail or ''}".strip(), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


async def _core_image(version: str | None) -> int:
    """Prints the image to update to (for deploy/update.sh), checked by this image's keys."""
    engine = create_engine(get_settings().owner_database_url)
    try:
        with working_as(Context(channel="cli:core")):
            registry = await _registry(engine)
        async with safe_client(allowed_hosts=hosts_for(registry or "")) as http:
            found, image = await verified_image(http, registry, version)
    except Problem as problem:
        print(f"{problem.title} {problem.detail or ''}".strip(), file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    print(f"The core {found}, signed.", file=sys.stderr)
    print(image)
    return 0


async def _backup_folder(new: str | None) -> int:
    """The setting "Backup folder": printed (for the deploy scripts), or set (at install)."""
    engine = create_engine(get_settings().owner_database_url)
    try:
        with working_as(Context(channel="cli:backup")):
            async with owner_transaction(engine) as conn:
                if new is not None:
                    try:
                        checked = FOLDER.check(new)
                    except Invalid as error:
                        print(str(error), file=sys.stderr)
                        return 2
                    await conn.execute(
                        text(
                            "UPDATE sys.setting SET value = CAST(:v AS jsonb)"
                            " WHERE name = :n AND deleted IS NULL"
                        ),
                        {"v": json.dumps(checked), "n": FOLDER.name},
                    )
                print(await value(conn, FOLDER.name) or "")
    finally:
        await engine.dispose()
    return 0


# The app's own "no" to the updater: not 1, which is also Docker's when the worker is restarting.
UPDATER_NO = 3


async def _updater(
    command: str, request: str | None, state: str | None, extra: dict[str, str]
) -> int:
    """For the updater service on the machine: what's due, starting it, how it went."""
    settings = get_settings()
    compose_modules([CORE, *discover(settings.modules_dir)])  # the notification kinds, among others
    engine = create_engine(settings.owner_database_url)
    try:
        with working_as(Context(channel="updater")):
            async with owner_transaction(engine) as conn:
                if command == "next":
                    found = await update_requests.due(conn)
                    if found:
                        print(found["sys_id"], found["target"], found["version"])
                    return 0
                if request is None:
                    print("Name the request.", file=sys.stderr)
                    return 2
                if command == "start":
                    started = await update_requests.start(conn, uuid.UUID(request))
                    return 0 if started else UPDATER_NO
                await update_requests.finish(
                    conn,
                    uuid.UUID(request),
                    state=state or "",
                    message=extra.get("message", ""),
                    backup=extra.get("backup") or None,
                    log=sys.stdin.read() if not sys.stdin.isatty() else "",
                )
    except Problem as problem:
        print(f"{problem.title} {problem.detail or ''}".strip(), file=sys.stderr)
        return UPDATER_NO
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="p42cloud")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "setup-database", help="create the roles and the core's schema (administrator, once)"
    )
    commands.add_parser(
        "upgrade", help="bring the database, the core and every installed module to this version"
    )
    commands.add_parser("check", help="refuse a database that isn't set up safely")
    modules = commands.add_parser(
        "module", help="list, install or update, switch on or off, remove or bring back"
    )
    modules.add_argument(
        "action", choices=["list", "updates", "install", "update", "on", "off", "remove"]
    )
    modules.add_argument("module", nargs="?")
    modules.add_argument("--version", help="install or update: this release, not the newest")
    modules.add_argument(
        "--file", type=Path, help="install or update: a package file, next to its .sig file"
    )
    updater = commands.add_parser("updater", help="for the updater service on the machine")
    updater.add_argument("updater_command", choices=["next", "start", "finish"])
    updater.add_argument("request", nargs="?")
    updater.add_argument("state", nargs="?", help="finish: succeeded, put_back or failed")
    updater.add_argument("--message", default="")
    updater.add_argument("--backup", default="")
    backup = commands.add_parser("backup", help="the backup folder (a setting)")
    folder = backup.add_subparsers(dest="backup_command", required=True).add_parser(
        "folder", help="print the setting Backup folder, or --set it"
    )
    folder.add_argument("--set", dest="new_folder", help="the folder on the machine")
    core = commands.add_parser("core", help="the core's releases")
    image = core.add_subparsers(dest="core_command", required=True).add_parser(
        "image", help="the signed image of the newest (or a given) release, to update to"
    )
    image.add_argument("--version", help="this release, not the newest")
    admin = commands.add_parser(
        "create-admin", help="the first administrator of a new installation (asks for a password)"
    )
    admin.add_argument("--name", required=True)
    admin.add_argument("--email", required=True)
    reset = commands.add_parser(
        "reset-second-factor",
        help="a locked-out person's authenticator apps and passkeys removed (audited)",
    )
    reset.add_argument("--email", required=True)
    token = commands.add_parser(
        "token", help="a short-lived API token acting as a person, for the operator (audited)"
    )
    token.add_argument("--email", required=True)
    token.add_argument("--name", default="Operator")
    token.add_argument("--hours", type=int, default=1, help="1 to 24 (default 1)")
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
    commands_run: dict[str, Callable[[], Coroutine[Any, Any, int]]] = {
        "setup-database": _set_up,
        "upgrade": _upgrade,
        "module": lambda: _module(
            args.action,
            args.module,
            Source(None, args.version, args.file),  # the registry: read from the settings
        ),
        "create-admin": lambda: _create_admin(args.name, args.email),
        "audit": lambda: _verify(args.anchor),
        "check": _check,
        "reset-second-factor": lambda: _recover("reset-second-factor", args.email, "", 0),
        "token": lambda: _recover("token", args.email, args.name, args.hours),
        "core": lambda: _core_image(args.version),
        "backup": lambda: _backup_folder(args.new_folder),
        "updater": lambda: _updater(
            args.updater_command,
            args.request,
            args.state,
            {"message": args.message, "backup": args.backup},
        ),
    }
    return asyncio.run(commands_run[args.command]())


if __name__ == "__main__":
    sys.exit(main())
