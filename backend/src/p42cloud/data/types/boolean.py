"""Yes or no."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare


class BooleanType(FieldType):
    name = "boolean"
    label = "Yes / no"

    def column_type(self, options: dict[str, Any]) -> str:
        return "boolean"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is not None and not isinstance(value, bool):
            raise Invalid("Must be yes or no (true or false).")
        return value


TYPE = BooleanType()


def Boolean(label: str, **common: Unpack[Common]) -> Field:
    return declare("boolean", label, {}, common)
