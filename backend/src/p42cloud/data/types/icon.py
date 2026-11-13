"""The name of a Material Symbol."""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare


class IconType(PatternType):
    name = "icon"
    label = "Icon"
    pattern = r"[a-z0-9_]{1,64}"
    column = "text"
    message = "Must be the name of an icon, like shopping_cart."


TYPE = IconType()


def Icon(label: str, **common: Unpack[Common]) -> Field:
    return declare("icon", label, {}, common)
