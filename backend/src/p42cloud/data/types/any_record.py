"""A record of any table, by its ID; another field says which table (no foreign key).

`table_field` names that field: a reference to sys.table, or a table's name (`dca.order`). With
it, the record linked to gets a related list of these records.
"""

import re
import uuid
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

FIELD = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class AnyRecordType(FieldType):
    name = "any_record"
    label = "Any record"
    kind = "reference"

    def keeps_values(self, old: Field, new: Field) -> bool:
        """Naming the table field (or another) only adds a related list: the IDs stay."""

        def own(options: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in options.items() if k != "table_field"}

        return old.type == new.type and own(old.options) == own(new.options)

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        found = super().option_problems({k: v for k, v in options.items() if k != "table_field"})
        given = options.get("table_field")
        if given is not None and (not isinstance(given, str) or not FIELD.match(given)):
            found.append("table_field must name a field of this table.")
        return found

    def to_json(self, value: Any) -> Any:
        return None if value is None else str(value)

    def column_type(self, options: dict[str, Any]) -> str:
        return "uuid"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None or isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except ValueError as error:
            raise Invalid("Must be the ID of a record.") from error


TYPE = AnyRecordType()


def AnyRecord(label: str, *, table_field: str | None = None, **common: Unpack[Common]) -> Field:
    """`table_field`: the field saying which table (gives the linked record a related list)."""
    options = {} if table_field is None else {"table_field": table_field}
    return declare("any_record", label, options, common)
