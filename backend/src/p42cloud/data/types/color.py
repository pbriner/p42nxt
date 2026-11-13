"""A colour as #rrggbb."""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare


class ColorType(PatternType):
    name = "color"
    label = "Colour"
    pattern = r"#[0-9a-f]{6}"
    column = "text"
    message = "Must be a colour like #1a73e8."

    def normalize(self, value: str) -> str:
        return value.strip().lower()


TYPE = ColorType()


def Color(label: str, **common: Unpack[Common]) -> Field:
    return declare("color", label, {}, common)
