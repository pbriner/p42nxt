"""A phone number as people write it."""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare


class PhoneType(PatternType):
    name = "phone"
    label = "Phone number"
    pattern = r"\+?[0-9 ()./-]{3,32}"
    column = "text"
    message = "Must be a phone number, like +41 44 123 45 67."


TYPE = PhoneType()


def Phone(label: str, **common: Unpack[Common]) -> Field:
    return declare("phone", label, {}, common)
