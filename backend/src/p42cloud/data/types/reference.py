"""A record of another table, by its ID; the database keeps the link valid."""

import re
import uuid
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

TABLE = re.compile(r"^[a-z][a-z0-9]{0,15}\.[a-z][a-z0-9_]{0,39}$")


class ReferenceType(FieldType):
    name = "reference"
    label = "Reference"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        found = super().option_problems({k: v for k, v in options.items() if k != "table"})
        table = options.get("table")
        if not isinstance(table, str) or not TABLE.match(table):
            found.append("table must name a table, like sys.table.")
        return found

    def column_type(self, options: dict[str, Any]) -> str:
        return "uuid"

    def references(self, options: dict[str, Any]) -> str | None:
        return str(options["table"])

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None or isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise Invalid("Must be the ID of a record.") from error


TYPE = ReferenceType()


def Reference(label: str, table: str, **common: Unpack[Common]) -> Field:
    return declare("reference", label, {"table": table}, common)
