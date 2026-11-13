"""A calendar day, without a time."""

from datetime import date, datetime
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare


class DateType(FieldType):
    name = "date"
    label = "Date"
    kind = "date"

    def to_json(self, value: Any) -> Any:
        return None if value is None else value.isoformat()

    def column_type(self, options: dict[str, Any]) -> str:
        return "date"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise Invalid("Must be a date like 2026-11-13.")


TYPE = DateType()


def Date(label: str, **common: Unpack[Common]) -> Field:
    return declare("date", label, {}, common)
