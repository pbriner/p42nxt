"""A record of any table, by its ID; another field says which table (no foreign key)."""

import uuid
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare


class AnyRecordType(FieldType):
    name = "any_record"
    label = "Any record"
    kind = "reference"

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


def AnyRecord(label: str, **common: Unpack[Common]) -> Field:
    return declare("any_record", label, {}, common)
