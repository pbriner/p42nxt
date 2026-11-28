"""The installation's own settings that used to be environment variables, and their values as
this process last read them.

They're settings (records), changed by administrators in the app and checked like the
environment checked them. Processes read them at start and every 30 seconds (`refresh`), so a
change takes effect within half a minute without a restart. While a setting is empty, a value
from the environment still counts (development and tests set them there); a value an
installation had in its environment becomes the setting on the upgrade that brings it.
"""

from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.settings import Setting
from p42cloud.data.types import Invalid
from p42cloud.kernel.config import Settings, get_settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.outgoing import set_delivery

REFRESH = 30.0  # seconds


def _production() -> bool:
    try:
        return get_settings().environment == "production"
    except ValueError:
        return True  # unknown: the stricter checks


def _address(given: Any) -> str:
    if given is None or given == "":
        return ""
    if not isinstance(given, str):
        raise Invalid("An address like https://p42.example.com.")
    address = urlsplit(given.strip())
    if (
        address.scheme not in ("http", "https")
        or not address.hostname
        or address.path
        not in (
            "",
            "/",
        )
    ):
        raise Invalid("An address like https://p42.example.com, with nothing after the name.")
    if _production() and address.scheme != "https" and address.hostname != "localhost":
        raise Invalid("In production the address must start with https://.")
    return given.strip().rstrip("/")


def _release_address(given: Any) -> str:
    if given is None or given == "":
        return ""
    if not isinstance(given, str) or urlsplit(given.strip()).scheme not in ("http", "https"):
        raise Invalid(
            "An address like https://github.com/<owner>/<repo>/releases/download/registry."
        )
    if _production() and not given.strip().startswith("https://"):
        raise Invalid("In production the address must start with https://.")
    return given.strip().rstrip("/")


def _whole(low: int, high: int, unit: str) -> Any:
    def check(given: Any) -> int:
        if isinstance(given, bool) or not isinstance(given, int) or not low <= given <= high:
            raise Invalid(f"From {low} to {high} {unit}.")
        return given

    return check


PUBLIC_URL = Setting(
    "sys.public_url",
    "The app's address",
    "",
    check=_address,
    help=(
        "The address people open the app at, like https://p42.example.com. Passkeys, sign-in"
        " providers and links in messages need it; empty, they're off."
    ),
    adopt="public_url",
)
RELEASE_ADDRESS = Setting(
    "sys.registry_url",
    "Release address",
    "",
    check=_release_address,
    help=(
        "Where signed releases of the core and the modules are published, like"
        " https://github.com/<owner>/<repo>/releases/download/registry. Empty: the installation"
        " never asks for updates. Nothing about the installation is sent there."
    ),
    # The image's address, not registry_url: that one is read whenever the setting is empty,
    # so clearing the setting couldn't turn updates off.
    adopt="release_address",
)
REQUESTS_PER_MINUTE = Setting(
    "sys.requests_per_minute",
    "Requests per minute",
    600,
    check=_whole(10, 100_000, "requests"),
    help="Per person, token or address: more are refused for the rest of the minute.",
    adopt="requests_per_minute",
)
SESSION_IDLE = Setting(
    "sys.session_idle_minutes",
    "Sign out after inactivity (minutes)",
    30,
    check=_whole(5, 24 * 60, "minutes"),
    help="A session ends after this long without use.",
    adopt="session_idle_minutes",
)
SESSION_MAX = Setting(
    "sys.session_max_hours",
    "Sign out at the latest (hours)",
    12,
    check=_whole(1, 24 * 30, "hours"),
    help="A session ends after this long in any case.",
    adopt="session_max_hours",
)


def _yes_no(given: Any) -> bool:
    if not isinstance(given, bool):
        raise Invalid("On or off.")
    return given


DELIVER_OUTSIDE = Setting(
    "sys.deliver_outside",
    "Deliver outside on a test installation",
    False,
    check=_yes_no,
    help=(
        "Off: an installation that isn't production holds messages (Telegram), outside calls"
        " and changes through connections, and lists them as held. On: they are sent, as in"
        " production. Production always sends; this has no effect there."
    ),
    adopt="deliver_outside",
)
SETTINGS = (
    PUBLIC_URL,
    RELEASE_ADDRESS,
    REQUESTS_PER_MINUTE,
    SESSION_IDLE,
    SESSION_MAX,
    DELIVER_OUTSIDE,
)

# What this process last read: setting name → value.
_LIVE: dict[str, Any] = {}


async def refresh(engine: AsyncEngine) -> None:
    """Reads the values of these settings again."""
    with working_as(Context(channel="settings")):
        async with transaction(engine, "sys") as conn:
            found = await conn.execute(
                text(
                    "SELECT name, value FROM sys.setting WHERE name = ANY(:n) AND deleted IS NULL"
                ),
                {"n": [s.name for s in SETTINGS]},
            )
            values = {str(r.name): r.value for r in found}
    _LIVE.clear()
    _LIVE.update(values)
    deliver = values.get(DELIVER_OUTSIDE.name)
    set_delivery(deliver if isinstance(deliver, bool) else None)


def _text(setting: Setting, environment: str | None) -> str | None:
    """The setting as last read; while it's empty (or not read yet), the environment's."""
    return str(_LIVE.get(setting.name) or "") or environment or None


def public_url(settings: Settings) -> str | None:
    return _text(PUBLIC_URL, settings.public_url)


def registry_url(settings: Settings) -> str | None:
    return _text(RELEASE_ADDRESS, settings.registry_url)


def requests_per_minute(settings: Settings) -> int:
    return int(_LIVE.get(REQUESTS_PER_MINUTE.name, settings.requests_per_minute))


def session_idle(settings: Settings) -> timedelta:
    return timedelta(minutes=int(_LIVE.get(SESSION_IDLE.name, settings.session_idle_minutes)))


def session_longest(settings: Settings) -> timedelta:
    return timedelta(hours=int(_LIVE.get(SESSION_MAX.name, settings.session_max_hours)))
