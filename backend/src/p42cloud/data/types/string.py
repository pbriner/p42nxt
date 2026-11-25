"""Text up to a declared length."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare, string_value

MAX_LENGTH = 4000


class StringType(FieldType):
    name = "string"
    label = "Text"
    kind = "text"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        rest = {k: v for k, v in options.items() if k not in ("max_length", "field_of", "kinds")}
        found = super().option_problems(rest)
        field_of, kinds = options.get("field_of"), options.get("kinds")
        if field_of is not None and not isinstance(field_of, str):
            found.append("field_of names the field holding a list view's name.")
        if kinds is not None and (
            field_of is None
            or not isinstance(kinds, list)
            or not all(isinstance(k, str) for k in kinds)  # pyright: ignore[reportUnknownVariableType]
        ):
            found.append("kinds lists the kinds of fields offered (number, date …), with field_of.")
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


def String(
    label: str,
    *,
    max_length: int = 255,
    field_of: str | None = None,
    kinds: tuple[str, ...] | None = None,
    **common: Unpack[Common],
) -> Field:
    """`field_of`: the value names a field of the table whose list view the record's field of
    that name gives (a widget's measured field); forms offer those fields, `kinds` only some."""
    options: dict[str, Any] = {"max_length": max_length}
    if field_of is not None:
        options["field_of"] = field_of
        if kinds is not None:
            options["kinds"] = list(kinds)
    return declare("string", label, options, common)
