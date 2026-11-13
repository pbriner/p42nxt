"""Themes as records, and the colours a theme may set."""

import re
from dataclasses import dataclass
from typing import Literal

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

THEME = Table(
    "theme",
    "Theme",
    {
        "name": String("Name", max_length=40, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "label": TranslatedText("Label", required=True),
        "description": TranslatedText("Description"),
        # {token: [light, dark]}; tokens it doesn't set keep the app's built-in colours.
        "colors": Json("Colours", required=True),
        "radius": Integer("Corner radius", required=True, default=4),
        "cards": Choice(
            "Cards", {"outlined": "Outlined", "raised": "Raised"}, required=True, default="outlined"
        ),
        "font": Choice(
            "Font", {"roboto": "Roboto", "system": "The system's"}, required=True, default="roboto"
        ),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Themes",
    display="label",
)

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
THEME_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_RADIUS = 24

# The colours a theme may set: the Material roles, the parts of the app's frame, the charts.
MATERIAL = (
    frozenset(
        f"{prefix}{role}"
        for role in ("primary", "secondary", "tertiary", "error")
        for prefix in ("", "on-")
    )
    | frozenset(
        f"{prefix}{role}-container"
        for role in ("primary", "secondary", "tertiary", "error")
        for prefix in ("", "on-")
    )
    | frozenset(
        {
            "surface",
            "on-surface",
            "on-surface-variant",
            "surface-variant",
            "surface-dim",
            "surface-bright",
            "surface-container-lowest",
            "surface-container-low",
            "surface-container",
            "surface-container-high",
            "surface-container-highest",
            "outline",
            "outline-variant",
            "inverse-surface",
            "inverse-on-surface",
            "inverse-primary",
            "shadow",
            "scrim",
            "background",
            "on-background",
        }
    )
)
FRAME = frozenset(
    {
        "page-background",
        "card",
        "toolbar",
        "nav-background",
        "nav-background-end",
        "on-nav",
        "on-nav-variant",
        "nav-accent",
        "nav-active",
        "on-nav-active",
    }
)
CHARTS = frozenset({*(f"chart-{n}" for n in range(1, 9)), "chart-other"})
TOKENS = MATERIAL | FRAME | CHARTS


@dataclass(frozen=True)
class Theme:
    name: str
    label: str | dict[str, str]
    colors: dict[str, tuple[str, str]]  # token: (light, dark)
    description: str | dict[str, str] | None = None
    radius: int = 4
    cards: Literal["outlined", "raised"] = "outlined"
    font: Literal["roboto", "system"] = "roboto"
