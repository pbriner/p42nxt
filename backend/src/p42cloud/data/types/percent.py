"""A share, stored as a fraction: 0.05 is 5 %."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, declare
from p42cloud.data.types.decimal import fit, number

PRECISION, SCALE = 12, 6


class PercentType(FieldType):
    name = "percent"
    label = "Percentage"

    def column_type(self, options: dict[str, Any]) -> str:
        return f"numeric({PRECISION},{SCALE})"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        return None if value is None else fit(number(value), PRECISION, SCALE)


TYPE = PercentType()


def Percent(label: str, **common: Unpack[Common]) -> Field:
    return declare("percent", label, {}, common)
