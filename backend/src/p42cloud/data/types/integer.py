"""A whole number."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

LIMIT = 2**63


class IntegerType(FieldType):
    name = "integer"
    label = "Whole number"
    kind = "number"

    def column_type(self, options: dict[str, Any]) -> str:
        return "bigint"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise Invalid("Must be a whole number.")
        if not -LIMIT <= value < LIMIT:
            raise Invalid("The number is too large.")
        return value


TYPE = IntegerType()


def Integer(label: str, **common: Unpack[Common]) -> Field:
    return declare("integer", label, {}, common)
