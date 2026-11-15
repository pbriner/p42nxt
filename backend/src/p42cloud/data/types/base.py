"""What every field type provides, and the field declaration modules write.

A type decides how a value is stored (column type and checks), which options a field of it takes,
how a value is checked, and which changes to a field keep every value.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypedDict

from p42cloud.data.sql import ident, literal

READ_ONLY = "read_only"  # an option every field type may have


class Invalid(ValueError):
    """A value or an option that doesn't fit the type; the message is for people."""


class Common(TypedDict, total=False):
    required: bool
    unique: bool
    default: Any
    help: str
    personal: bool
    # Set by the platform or the module (a check's result, a run's state), shown but not edited
    # in forms. Kept in the options: `read_only`.
    read_only: bool


@dataclass(frozen=True)
class Field:
    """A field as declared, and as its record holds it."""

    type: str
    label: dict[str, str]
    options: dict[str, Any] = field(default_factory=dict[str, Any])
    required: bool = False
    unique: bool = False
    default: Any = None
    help: dict[str, str] | None = None
    personal: bool = False


def declare(type_name: str, label: str, options: dict[str, Any], common: Common) -> Field:
    help_text = common.get("help")
    if common.get("read_only"):
        options = options | {READ_ONLY: True}
    return Field(
        type=type_name,
        label={"en": label},
        options=options,
        required=common.get("required", False),
        unique=common.get("unique", False),
        default=common.get("default"),
        help={"en": help_text} if help_text else None,
        personal=common.get("personal", False),
    )


class FieldType:
    name: ClassVar[str]
    label: ClassVar[str]
    # Masked in the audit log: that it changed, never the values.
    secret: ClassVar[bool] = False
    # How lists filter and sort it (conditions.py): text, translated, number, date, datetime,
    # choice, reference or boolean. None: not filterable or sortable.
    kind: ClassVar[str | None] = None

    def to_json(self, value: Any) -> Any:
        """The stored value as JSON: exact numbers and times as text, never floats."""
        return value

    def sql_literal(self, value: Any, options: dict[str, Any]) -> str:
        """A value as a constant in a definition (a new column's value for existing records)."""
        column = self.column_type(options)
        stored = self.check(value, options)
        if column == "jsonb":
            return f"{literal(json.dumps(stored))}::jsonb"
        return f"{literal(str(self.to_json(stored)))}::{column}"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        """What's wrong with a field's options; empty if fine."""
        return [f"Unknown option {key!r}." for key in options]

    def column_type(self, options: dict[str, Any]) -> str:
        """As PostgreSQL's format_type() writes it, so the engine can compare."""
        raise NotImplementedError

    def checks(self, column: str, options: dict[str, Any]) -> list[str]:
        """CHECK expressions every value must pass, in the database."""
        return []

    def references(self, options: dict[str, Any]) -> str | None:
        """The table (`schema.name`) a value points to, if any."""
        return None

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        """A value from outside, checked and in its stored form; None stays None."""
        raise NotImplementedError

    def keeps_values(self, old: Field, new: Field) -> bool:
        """Whether a field of type `old` can become `new` without losing or refusing a value.

        Only called with `new.type == self.name`. The same type with the same options always can.
        """
        return old.type == new.type and old.options == new.options


def string_value(value: Any) -> str:
    if not isinstance(value, str):
        raise Invalid("Must be text.")
    return value


def pattern_check(column: str, pattern: str) -> str:
    return f"{ident(column)} ~ {literal(pattern)}"


class PatternType(FieldType):
    """Text that must match a pattern (email, colour …), checked in Python and in the database."""

    pattern: ClassVar[str]
    column: ClassVar[str] = "text"
    message: ClassVar[str]
    kind = "text"

    def column_type(self, options: dict[str, Any]) -> str:
        return self.column

    def checks(self, column: str, options: dict[str, Any]) -> list[str]:
        return [pattern_check(column, self.pattern)]

    def normalize(self, value: str) -> str:
        return value.strip()

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        text = self.normalize(string_value(value))
        if not re.fullmatch(self.pattern, text):
            raise Invalid(self.message)
        return text
