"""A length of time: days, hours, minutes and seconds (ISO 8601, like P1DT2H30M)."""

import re
from datetime import timedelta
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

# No years or months: their length depends on the date, so they can't be one duration.
ISO = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


class DurationType(FieldType):
    name = "duration"
    label = "Duration"

    def column_type(self, options: dict[str, Any]) -> str:
        return "interval"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None or isinstance(value, timedelta):
            return value
        found = ISO.match(value) if isinstance(value, str) else None
        if not found or value in ("P", "PT") or value.endswith("T"):
            raise Invalid("Must be a duration like P1DT2H30M (1 day, 2 hours, 30 minutes).")
        parts = {k: float(v) for k, v in found.groupdict().items() if v}
        return timedelta(**parts)


TYPE = DurationType()


def Duration(label: str, **common: Unpack[Common]) -> Field:
    return declare("duration", label, {}, common)
