"""A record of another table, by its ID; the database keeps the link valid.

A reference can be filled from a key instead (`key=`): another field of the record names the
target by its key (`match=`, its `name` by default, or fields joined by dots, like
`schema.name` for tables), and the database sets the link on every write. Records whose code
goes by names (a run's job, a view's table) link their targets that way, and get related lists.
"""

import re
import uuid
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

TABLE = re.compile(r"^[a-z][a-z0-9]{0,15}\.[a-z][a-z0-9_]{0,39}$")
FIELD = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MATCH = re.compile(r"^[a-z][a-z0-9_]{0,39}(\.[a-z][a-z0-9_]{0,39}){0,2}$")


class ReferenceType(FieldType):
    name = "reference"
    label = "Reference"
    kind = "reference"

    def to_json(self, value: Any) -> Any:
        return None if value is None else str(value)

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        own = ("table", "key", "match")
        found = super().option_problems({k: v for k, v in options.items() if k not in own})
        table = options.get("table")
        if not isinstance(table, str) or not TABLE.match(table):
            found.append("table must name a table, like sys.table.")
        key, match = options.get("key"), options.get("match")
        if key is not None and (not isinstance(key, str) or not FIELD.match(key)):
            found.append("key must name a field of this table.")
        if match is not None and (
            key is None or not isinstance(match, str) or not MATCH.match(match)
        ):
            found.append("match must name the target's key fields (like name), with a key.")
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


def Reference(
    label: str,
    table: str,
    *,
    key: str | None = None,
    match: str = "name",
    **common: Unpack[Common],
) -> Field:
    """`key`: the field naming the target by its `match` (the database fills the link)."""
    options: dict[str, Any] = {"table": table}
    if key is not None:
        options |= {"key": key, "match": match}
    return declare("reference", label, options, common)
