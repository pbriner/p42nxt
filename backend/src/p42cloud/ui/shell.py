"""What the app's frame shows about the installation itself."""

from typing import Any

from p42cloud.data.settings import Setting
from p42cloud.data.types import Invalid

STAGES = ("production", "qa", "test", "development")
LANGUAGES = ("en", "de")
MAX_NAME = 40


def _stage(given: Any) -> str:
    if given not in STAGES:
        raise Invalid(f"One of {', '.join(STAGES)}.")
    return str(given)


def _name(given: Any) -> str:
    if not isinstance(given, str) or not 1 <= len(given.strip()) <= MAX_NAME:
        raise Invalid(f"A name of 1 to {MAX_NAME} characters.")
    return given.strip()


def _language(given: Any) -> str:
    if given not in LANGUAGES:
        raise Invalid(f"One of {', '.join(LANGUAGES)}.")
    return str(given)


APP_NAME = Setting(
    "sys.app_name",
    "Name",
    "P42cloud",
    check=_name,
    help="The installation's name: in the navigator, on the sign-in page and in the browser tab.",
    public=True,
)

LANGUAGE = Setting(
    "sys.language",
    "Language",
    "en",
    check=_language,
    help="For people whose browser asks for a language the app doesn't speak.",
    public=True,
)

STAGE = Setting(
    "sys.stage",
    "Stage",
    "production",
    check=_stage,
    help="What this installation is for. Anything but production shows a badge in the top bar.",
    public=True,
)
