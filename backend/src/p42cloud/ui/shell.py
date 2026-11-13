"""What the app's frame shows about the installation itself."""

from typing import Any

from p42cloud.data.settings import Setting
from p42cloud.data.types import Invalid

STAGES = ("production", "qa", "test", "development")


def _stage(given: Any) -> str:
    if given not in STAGES:
        raise Invalid(f"One of {', '.join(STAGES)}.")
    return str(given)


STAGE = Setting(
    "sys.stage",
    "Stage",
    "production",
    check=_stage,
    help="What this installation is for. Anything but production shows a badge in the top bar.",
    public=True,
)
