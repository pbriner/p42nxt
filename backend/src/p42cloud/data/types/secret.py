"""A secret: written, never read back. Absent from every answer, masked in the audit log.

What a secret holds is up to the feature storing it: a hash (passwords) or a value sealed with
the master key (second factors).
"""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, declare, string_value


class SecretType(FieldType):
    name = "secret"
    label = "Secret"
    secret = True

    def column_type(self, options: dict[str, Any]) -> str:
        return "text"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        return None if value is None else string_value(value)


TYPE = SecretType()


def Secret(label: str, **common: Unpack[Common]) -> Field:
    return declare("secret", label, {}, common)
