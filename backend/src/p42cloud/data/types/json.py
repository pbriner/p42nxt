"""Technical data as JSON. With `lines`, a list of short texts, edited one per line."""

import json
import re
from typing import Any, Unpack, cast

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

_SEPARATORS = re.compile(r"[\n,;]+")


def _lines(value: Any) -> list[str]:
    """A list of texts: given as a list, or as one text with a line, comma or semicolon between
    them (how people type them). Blank ones are dropped."""
    if isinstance(value, str):
        value = _SEPARATORS.split(value)
    if not isinstance(value, list):
        raise Invalid("Must be a list of texts.")
    found: list[str] = []
    for item in cast(list[Any], value):
        if not isinstance(item, str):
            raise Invalid("Must be a list of texts.")
        if item.strip():
            found.append(item.strip())
    return found


class JsonType(FieldType):
    name = "json"
    label = "JSON"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        rest = {k: v for k, v in options.items() if k != "lines"}
        found = super().option_problems(rest)
        if "lines" in options and not isinstance(options["lines"], bool):
            found.append("lines is true or false.")
        return found

    def column_type(self, options: dict[str, Any]) -> str:
        return "jsonb"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if options.get("lines") and value is not None:
            return _lines(value) or None
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise Invalid("Must be JSON data.") from error
        return value

    def keeps_values(self, old: Field, new: Field) -> bool:
        # `lines` only changes how new values are typed: what's stored stays as it is.
        def bare(options: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in options.items() if k != "lines"}

        return old.type == new.type and bare(old.options) == bare(new.options)


TYPE = JsonType()


def Json(label: str, *, lines: bool = False, **common: Unpack[Common]) -> Field:
    """`lines`: a list of short texts (addresses, names), one per line in forms."""
    return declare("json", label, {"lines": True} if lines else {}, common)
