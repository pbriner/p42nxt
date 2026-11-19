"""A record's number (INV-0000123): given by the database from a counter of its own, never typed,
never changed and never given twice, not even after a record is deleted."""

import re
from typing import Any, Unpack

from p42cloud.data.types.base import (
    Common,
    Field,
    FieldType,
    Invalid,
    declare,
    pattern_check,
    string_value,
)

MAX_PREFIX = 10
MIN_DIGITS, MAX_DIGITS = 1, 12
_PREFIX = re.compile(r"^[A-Z][A-Z0-9]*$")


def shape(options: dict[str, Any]) -> str:
    """The pattern every number of this field matches; it may outgrow its digits."""
    prefix = options.get("prefix") or ""
    lead = f"{prefix}-" if prefix else ""
    return f"^{lead}[0-9]{{{int(options['digits'])},}}$"


class NumberType(FieldType):
    name = "number"
    label = "Record number"
    kind = "text"
    generated = True

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        found = super().option_problems(
            {k: v for k, v in options.items() if k not in ("prefix", "digits")}
        )
        prefix = options.get("prefix")
        if prefix is not None and (
            not isinstance(prefix, str) or len(prefix) > MAX_PREFIX or not _PREFIX.match(prefix)
        ):
            found.append(
                f"prefix is up to {MAX_PREFIX} capital letters and digits, starting with a letter."
            )
        digits = options.get("digits")
        if (
            not isinstance(digits, int)
            or isinstance(digits, bool)
            or not (MIN_DIGITS <= digits <= MAX_DIGITS)
        ):
            found.append(f"digits must be a whole number from {MIN_DIGITS} to {MAX_DIGITS}.")
        return found

    def column_type(self, options: dict[str, Any]) -> str:
        return "text"

    def checks(self, column: str, options: dict[str, Any]) -> list[str]:
        return [pattern_check(column, shape(options))]

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        text = string_value(value).strip()
        if not re.fullmatch(shape(options), text):
            raise Invalid("Not a number of this field.")
        return text


TYPE = NumberType()


def Number(
    label: str, *, prefix: str | None = None, digits: int = 7, **common: Unpack[Common]
) -> Field:
    """A record number from the database: `Number("Invoice", prefix="INV")` gives INV-0000001."""
    options: dict[str, Any] = {"digits": digits}
    if prefix is not None:
        options["prefix"] = prefix
    return declare("number", label, options, common)
