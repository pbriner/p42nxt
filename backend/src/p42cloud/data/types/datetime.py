"""A moment in time, stored in UTC; it needs a time zone, so it's never ambiguous."""

from datetime import UTC, datetime
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare


class DateTimeType(FieldType):
    name = "datetime"
    label = "Date and time"
    kind = "datetime"

    def to_json(self, value: Any) -> Any:
        return None if value is None else value.isoformat()

    def column_type(self, options: dict[str, Any]) -> str:
        return "timestamp with time zone"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        moment = value
        if isinstance(value, str):
            try:
                moment = datetime.fromisoformat(value)
            except ValueError as error:
                raise Invalid("Must be a date and time like 2026-11-13T09:30:00Z.") from error
        if not isinstance(moment, datetime):
            raise Invalid("Must be a date and time like 2026-11-13T09:30:00Z.")
        if moment.tzinfo is None:
            raise Invalid("Needs a time zone, like 2026-11-13T09:30:00Z or +01:00.")
        return moment.astimezone(UTC)


TYPE = DateTimeType()


def DateTime(label: str, **common: Unpack[Common]) -> Field:
    return declare("datetime", label, {}, common)
