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


def iso_duration(value: timedelta) -> str:
    sign, value = ("-", -value) if value < timedelta(0) else ("", value)
    hours, rest = divmod(value.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    second = f"{seconds}.{value.microseconds:06d}".rstrip("0").rstrip(".")
    time = "".join(
        f"{n}{unit}"
        for n, unit in ((hours, "H"), (minutes, "M"), (second, "S"))
        if n not in (0, "0")
    )
    days = f"{value.days}D" if value.days else ""
    if not days and not time:
        return "PT0S"
    return f"{sign}P{days}{'T' + time if time else ''}"


class DurationType(FieldType):
    name = "duration"
    label = "Duration"
    kind = "number"

    def to_json(self, value: Any) -> Any:
        return None if value is None else iso_duration(value)

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
