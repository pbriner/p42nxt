"""A web address (http or https only, so a link never runs a script)."""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare


class UrlType(PatternType):
    name = "url"
    label = "Web address"
    pattern = r"https?://\S+"
    column = "text"
    message = "Must be a web address starting with http:// or https://."


TYPE = UrlType()


def Url(label: str, **common: Unpack[Common]) -> Field:
    return declare("url", label, {}, common)
