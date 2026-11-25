"""The web installer's work: the first administrator, the installation's name and language, and
which modules stay on.

Open only while the installation has no active administrator, and only with the one-time setup
code from install.sh. Everything about the people and settings happens in one transaction, under
a lock, so two people setting up at once can't both succeed. The administrator then signs in
and sets up their second factor like anyone without one.
"""

import hmac
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.installation import RELEASE_ADDRESS
from p42cloud.data.records import Records
from p42cloud.data.settings import Setting, value
from p42cloud.data.types import Invalid
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.modules.install import check_updates_soon
from p42cloud.modules.lifecycle import switch_off
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.sign_in import credentials, passwords
from p42cloud.security.sign_in.events import event
from p42cloud.services.backups.manifest import FOLDER

# Any number: the lock that keeps two setups from running at once.
_LOCK = 4242_0001
_ADMINS = text(
    "SELECT count(*) FROM sys.membership m JOIN sys.person p ON p.sys_id = m.person"
    ' WHERE m."group" = :group AND m.deleted IS NULL AND p.deleted IS NULL AND p.active'
)


@dataclass(frozen=True)
class Setup:
    code: str
    language: str
    name: str  # the installation's (the company's)
    admin_name: str
    email: str
    password: str
    modules: frozenset[str]  # the optional modules to keep on
    backups: bool  # set up now; False: postponed, knowingly
    master_key: bool  # a copy is stored safely
    backup_folder: str | None = None  # None: the one install.sh made stays
    release_address: str | None = None  # None: the image's stays ("" asks for no updates)


async def _open(conn: AsyncConnection) -> bool:
    return not await conn.scalar(_ADMINS, {"group": ADMINISTRATORS})


async def is_open(engine: AsyncEngine) -> bool:
    """Whether the installer still runs: nobody administers the installation yet."""
    with working_as(Context(channel="setup", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            return await _open(conn)


def _check_code(expected: str | None, given: str) -> None:
    if not expected:
        raise Problem(
            409, "no_setup_code", "This installation is set up at the command line (create-admin)."
        )
    if not hmac.compare_digest(expected.strip().upper().encode(), given.strip().upper().encode()):
        raise Problem(403, "wrong_setup_code", "That isn't the setup code.")


# The settings the installer asks for, by the name its body gives them.
MACHINE: dict[str, Setting] = {"backup_folder": FOLDER, "release_address": RELEASE_ADDRESS}


async def machine_settings(engine: AsyncEngine) -> dict[str, str]:
    """The backup folder install.sh made and the release address the image brought, to keep or
    change while setting up."""
    with working_as(Context(channel="setup", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            return {key: str(await value(conn, s.name) or "") for key, s in MACHINE.items()}


def _checked(given: Setup) -> list[tuple[str, Any]]:
    """The machine settings given, checked before anything is written, each error by its field."""
    chosen: list[tuple[str, Any]] = []
    errors: list[FieldError] = []
    for key, setting in MACHINE.items():
        wanted = getattr(given, key)
        if wanted is None:
            continue
        try:
            chosen.append((setting.name, setting.check(wanted)))
        except Invalid as invalid:
            errors.append(FieldError(key, "invalid", str(invalid)))
    if errors:
        raise Problem(422, "validation", "Some fields aren't valid.", errors=errors)
    return chosen


async def optional_modules(engine: AsyncEngine) -> list[dict[str, Any]]:
    """The installed modules, which may be switched off and which can't (core dependencies)."""
    with working_as(Context(channel="setup", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            rows = await conn.execute(
                text(
                    "SELECT name, label, description, depends_on, protected, enabled"
                    " FROM sys.module WHERE deleted IS NULL ORDER BY protected DESC, label"
                )
            )
            return [
                {
                    "id": r.name,
                    "name": r.label,
                    "description": r.description,
                    "depends_on": sorted(r.depends_on or {}),
                    "required": r.protected,
                    "enabled": r.enabled,
                }
                for r in rows
            ]


async def set_up(
    engine: AsyncEngine, owner: AsyncEngine, expected_code: str | None, given: Setup
) -> None:
    """Makes the first administrator and the settings, then switches off the modules not kept."""
    _check_code(expected_code, given.code)
    if not given.master_key:
        raise Problem(422, "confirm", "Store a copy of the master key first, and confirm it.")
    passwords.check(given.password, given.email)
    machine = _checked(given)
    with working_as(Context(channel="setup", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK})
            if not await _open(conn):
                raise Problem(409, "set_up", "This installation is set up already. Sign in.")
            records = Records(conn, await load_catalog(conn))
            person = (
                await records.create(
                    "sys.person",
                    {"name": given.admin_name, "email": given.email, "emergency_access": True},
                )
            ).sys_id
            await records.create("sys.membership", {"group": ADMINISTRATORS, "person": person})
            await credentials.set_password(conn, person, given.password)
            chosen: list[tuple[str, Any]] = [
                ("sys.app_name", given.name),
                ("sys.language", given.language),
                *machine,
            ]
            for name, value in chosen:
                found = await records.list(
                    "sys.setting", where={"field": "name", "op": "eq", "value": name}
                )
                setting = found.records[0]
                await records.update("sys.setting", setting.sys_id, {"value": value}, setting.etag)
            await event(
                conn,
                "set_up",
                person=str(person),
                backups="set up" if given.backups else "postponed",
                master_key="stored",
            )
            # A new installation knows only the modules it came with; the registry's are offered
            # once the update check has run, which shouldn't wait for its daily time.
            await check_updates_soon(conn, "setup")
        installed = await optional_modules(engine)
    await _keep_only(owner, installed, given.modules)


def _kept(installed: list[dict[str, Any]], chosen: frozenset[str]) -> set[str]:
    """The modules chosen, the ones they need (directly or not), and the core's."""
    needs = {str(m["id"]): set(cast(list[str], m["depends_on"])) for m in installed}
    kept = {str(m["id"]) for m in installed if m["required"]} | (set(chosen) & set(needs))
    waiting = list(kept)
    while waiting:
        for other in needs.get(waiting.pop(), set()):
            if other not in kept:
                kept.add(other)
                waiting.append(other)
    return kept


async def _keep_only(
    owner: AsyncEngine, installed: list[dict[str, Any]], chosen: frozenset[str]
) -> None:
    """Switches off the modules not kept, those that need others first."""
    kept = _kept(installed, chosen)
    left = [m for m in installed if m["enabled"] and str(m["id"]) not in kept]
    with working_as(Context(channel="setup", access=EVERYTHING)):
        while left:
            # A module no other leaving module needs can go now; with a circle, one at a time.
            ready = [
                m
                for m in left
                if not any(m["id"] in cast(list[str], o["depends_on"]) for o in left if o is not m)
            ] or left[:1]
            for module in ready:
                await switch_off(owner, str(module["id"]))
            left = [m for m in left if m not in ready]
