"""Form rules as records, and how modules declare them."""

from dataclasses import dataclass
from typing import Any

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String

FORM_RULE = Table(
    "form_rule",
    "Form rule",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "table": String("Table name", max_length=80, required=True),
        "table_ref": Reference("Table", "sys.table", key="table", match="schema.name"),
        # When it applies: the filter language, in the part the browser evaluates too.
        "condition": Json("When"),
        "hide": Json("Hide"),
        "required": Json("Required"),
        "read_only": Json("Read-only"),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Form rules",
)

# The part of the filter language a rule may use: the browser evaluates it while the person
# types, the server again on saving, and both must agree.
RULE_OPERATORS = frozenset(
    {
        "eq",
        "ne",
        "in",
        "not_in",
        "empty",
        "not_empty",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        "lt",
        "lte",
        "gt",
        "gte",
        "between",
    }
)
RULE_VARIABLES = frozenset({"@me"})


@dataclass(frozen=True)
class FormRule:
    """While `condition` holds (None: always), these fields hide, are required or read-only.

    A rule only ever hides or locks more: what roles allow to read or change stays the limit.
    """

    name: str  # unique in the module; the record is named <module>.<name>
    table: str
    condition: Any = None
    hide: tuple[str, ...] = ()
    required: tuple[str, ...] = ()
    read_only: tuple[str, ...] = ()
