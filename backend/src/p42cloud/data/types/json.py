"""Technical data as JSON, read-only in the interface."""

import json
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare


class JsonType(FieldType):
    name = "json"
    label = "JSON"

    def column_type(self, options: dict[str, Any]) -> str:
        return "jsonb"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise Invalid("Must be JSON data.") from error
        return value


TYPE = JsonType()


def Json(label: str, **common: Unpack[Common]) -> Field:
    return declare("json", label, {}, common)
