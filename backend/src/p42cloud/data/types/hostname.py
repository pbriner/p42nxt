"""A host name: lower case, without the trailing dot."""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare


class HostnameType(PatternType):
    name = "hostname"
    label = "Host name"
    pattern = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*"
    column = "character varying(253)"
    message = "Must be a host name, like mail.example.com."

    def normalize(self, value: str) -> str:
        return value.strip().lower().removesuffix(".")


TYPE = HostnameType()


def Hostname(label: str, **common: Unpack[Common]) -> Field:
    return declare("hostname", label, {}, common)
