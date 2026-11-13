"""Text formatted with Markdown, stored as written."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, declare, string_value


class MarkdownType(FieldType):
    name = "markdown"
    label = "Formatted text"

    def column_type(self, options: dict[str, Any]) -> str:
        return "text"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        return None if value is None else string_value(value)

    def keeps_values(self, old: Field, new: Field) -> bool:
        return old.type in {"text", "markdown", "string"}


TYPE = MarkdownType()


def Markdown(label: str, **common: Unpack[Common]) -> Field:
    return declare("markdown", label, {}, common)
