"""One of a declared list of values, in declared order, each with a translated label."""

import re
from typing import Any, Unpack, cast

from p42cloud.data.sql import ident, literal
from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare, string_value

VALUE = re.compile(r"^[a-z0-9_]{1,40}$")


def values_of(options: dict[str, Any]) -> list[str]:
    return [entry["value"] for entry in options["values"]]


class ChoiceType(FieldType):
    name = "choice"
    label = "Choice"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        found = super().option_problems({k: v for k, v in options.items() if k != "values"})
        entries = options.get("values")
        if not isinstance(entries, list) or not entries:
            return [*found, "values must list at least one choice."]
        seen: set[str] = set()
        for entry in cast(list[Any], entries):
            value: Any = (
                cast(dict[str, Any], entry).get("value") if isinstance(entry, dict) else None
            )
            if not isinstance(value, str) or not VALUE.match(value):
                found.append(f"Not a choice value: {value!r} (a-z, 0-9 and _).")
            elif value in seen:
                found.append(f"The choice {value!r} is listed twice.")
            else:
                seen.add(value)
        return found

    def column_type(self, options: dict[str, Any]) -> str:
        return "text"

    def checks(self, column: str, options: dict[str, Any]) -> list[str]:
        listed = ", ".join(literal(v) for v in values_of(options))
        return [f"{ident(column)} IN ({listed})"]

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        if string_value(value) not in values_of(options):
            raise Invalid(f"Must be one of: {', '.join(values_of(options))}.")
        return value

    def keeps_values(self, old: Field, new: Field) -> bool:
        return old.type == "choice" and set(values_of(old.options)) <= set(values_of(new.options))


TYPE = ChoiceType()


def Choice(label: str, values: dict[str, str], **common: Unpack[Common]) -> Field:
    """`values` maps each stored value to its English label, in the order shown."""
    entries = [{"value": value, "label": {"en": text}} for value, text in values.items()]
    return declare("choice", label, {"values": entries}, common)
