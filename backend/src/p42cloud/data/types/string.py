"""Text up to a declared length."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare, string_value

MAX_LENGTH = 4000


class StringType(FieldType):
    name = "string"
    label = "Text"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        rest = {k: v for k, v in options.items() if k != "max_length"}
        found = super().option_problems(rest)
        length = options.get("max_length")
        if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= MAX_LENGTH:
            found.append(f"max_length must be a whole number from 1 to {MAX_LENGTH}.")
        return found

    def column_type(self, options: dict[str, Any]) -> str:
        return f"character varying({int(options['max_length'])})"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        text = string_value(value)
        if len(text) > options["max_length"]:
            raise Invalid(f"Must be at most {options['max_length']} characters.")
        return text

    def keeps_values(self, old: Field, new: Field) -> bool:
        return old.type == "string" and old.options["max_length"] <= new.options["max_length"]


TYPE = StringType()


def String(label: str, *, max_length: int = 255, **common: Unpack[Common]) -> Field:
    return declare("string", label, {"max_length": max_length}, common)
