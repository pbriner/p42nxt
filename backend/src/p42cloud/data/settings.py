"""Settings: records (`sys.setting`), declared by modules with a default and a check.

    Setting("sys.time_zone", "Time zone", "UTC", check=time_zone, public=True)

Installing a module writes its settings with their defaults; a value someone changed is never
overwritten. Changing a value is checked by the setting's own check. Public settings (the time
zone) may be read by everyone; the others by those with a grant.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.declarations import Table
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.types import Invalid
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.json import Json
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.kernel.config import get_settings
from p42cloud.kernel.errors import FieldError, Problem

SETTING = Table(
    "setting",
    "Setting",
    {
        "name": String("Name", max_length=80, required=True, unique=True),
        "label": String("Label", max_length=120, required=True),
        "help": Text("Help"),
        "value": Json("Value"),
        "public": Boolean("Everyone may read it", required=True, default=False),
    },
    label_plural="Settings",
)


@dataclass(frozen=True)
class Setting:
    name: str  # <module>.<name>
    label: str
    default: Any
    # Checks a new value and returns it as stored; raises Invalid with a message for people.
    check: Callable[[Any], Any]
    help: str = ""
    public: bool = False
    # A value the environment gives (a Settings field: one from before this was a setting, or one
    # the image brings): taken over once, when the setting first comes.
    adopt: str | None = None


# The settings of the installed modules, by name (filled when modules are composed).
DECLARED: dict[str, Setting] = {}


def declare(*settings: Setting) -> None:
    for setting in settings:
        DECLARED[setting.name] = setting


async def value(conn: AsyncConnection, name: str) -> Any:
    """A setting's value, its default if it was never written."""
    found = (
        await conn.execute(
            text("SELECT value FROM sys.setting WHERE name = :n AND deleted IS NULL"), {"n": name}
        )
    ).first()
    if found is not None:
        return found.value
    declared = DECLARED.get(name)
    return declared.default if declared else None


def _adopted(setting: Setting) -> Any:
    """The environment's value for a setting that used to come from there, checked; else None."""
    if setting.adopt is None:
        return None
    try:
        given = getattr(get_settings(), setting.adopt, None)
    except ValueError:  # no environment to read (a test, a tool): nothing to take over
        return None
    if given is None:
        return None
    try:
        return setting.check(given)
    except Invalid:
        return None


async def write(conn: AsyncConnection, module: str, settings: tuple[Setting, ...]) -> None:
    """A module's settings as records: new ones with their default, labels kept current, values
    never overwritten; settings the module no longer declares are retired."""
    for setting in settings:
        first = _adopted(setting)
        params = {
            "name": setting.name,
            "label": setting.label[:120],
            "help": setting.help or None,
            "public": setting.public,
            "value": json.dumps(setting.default if first is None else first),
        }
        changed = await conn.execute(
            text(
                "UPDATE sys.setting SET label = :label, help = :help, public = :public,"
                " deleted = NULL WHERE name = :name"
            ),
            params,
        )
        if changed.rowcount == 0:
            await conn.execute(
                text(
                    "INSERT INTO sys.setting (name, label, help, public, value)"
                    " VALUES (:name, :label, :help, :public, CAST(:value AS jsonb))"
                ),
                params,
            )
    await conn.execute(
        text(
            "UPDATE sys.setting SET deleted = now() WHERE deleted IS NULL"
            " AND name LIKE :prefix AND NOT name = ANY(:names)"
        ),
        {"prefix": f"{module}.%", "names": [s.name for s in settings]},
    )


async def _check(change: Change) -> None:
    """Settings come from modules: only their values change, and each value is checked."""
    if change.operation in ("create", "delete", "restore"):
        raise Problem(409, "declared_only", "Settings come with their modules; change their value.")
    if set(change.values) - {"value"}:
        raise Problem(409, "declared_only", "Only a setting's value can be changed.")
    if "value" not in change.values or change.sys_id is None:
        return
    name = await change.records.conn.scalar(
        text("SELECT name FROM sys.setting WHERE sys_id = :id"), {"id": change.sys_id}
    )
    declared = DECLARED.get(str(name))
    if declared is None:
        raise Problem(409, "unknown_setting", "No module declares this setting any more.")
    try:
        change.values["value"] = declared.check(change.values["value"])
    except Invalid as error:
        raise Problem(
            422,
            "validation",
            "Some fields aren't valid.",
            errors=[FieldError("value", "invalid", str(error))],
        ) from error


TABLE_HOOKS = (TableHook("sys.setting", before=_check),)


def _time_zone(given: Any) -> str:
    if not isinstance(given, str):
        raise Invalid("A time zone, like Europe/Zurich.")
    try:
        ZoneInfo(given)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise Invalid(f"No time zone {given!r}; use one like Europe/Zurich.") from error
    return given


TIME_ZONE = Setting(
    "sys.time_zone",
    "Time zone",
    "UTC",
    check=_time_zone,
    help="Where the installation's days begin: for today, this week and dates in lists.",
    public=True,
)
