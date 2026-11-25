"""State flows and their transitions as records, and how modules declare them."""

from dataclasses import dataclass
from typing import Any

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText
from p42cloud.services.approvals.models import Approval

STATE_FLOW = Table(
    "state_flow",
    "State flow",
    {
        # <module>.<table>.<field>
        "name": String("Name", max_length=160, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "table": String("Table", max_length=80, required=True),
        "field": String("Field", max_length=40, required=True),
        "initial": String("Starts in", max_length=60, required=True),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="State flows",
)

STATE_TRANSITION = Table(
    "state_transition",
    "State transition",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "flow": String("Flow name", max_length=160, required=True),
        "flow_ref": Reference("Flow", "sys.state_flow", key="flow"),
        "label": TranslatedText("Label", required=True),
        # Empty: from any state.
        "from_state": String("From", max_length=60),
        "to_state": String("To", max_length=60, required=True),
        # Role names; empty: whoever may change the record.
        "roles": Json("Only for roles"),
        # The filter language: the record must match it as it is before the move.
        "condition": Json("Only when"),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="State transitions",
    display="label",
)

TABLES = [STATE_FLOW, STATE_TRANSITION]


@dataclass(frozen=True)
class Transition:
    """One allowed move of a state flow; it shows as a button where it applies."""

    name: str  # unique in the module
    label: str | dict[str, str]
    from_state: str | None  # None: from any state
    to_state: str
    roles: tuple[str, ...] = ()  # only for these roles; empty: whoever may change the record
    condition: Any = None  # only when the record matches (the filter language)
    icon: str | None = None
    confirm: str | dict[str, str] | None = None
    # The move needs other people's yes first (services/approvals); its button asks for it.
    approval: Approval | None = None


@dataclass(frozen=True)
class Flow:
    """The states a choice field of a table goes through: where it starts, and the only moves
    allowed. Changing the field any other way is refused."""

    table: str  # <module>.<table>
    field: str  # a choice field of the table
    initial: str
    transitions: tuple[Transition, ...]
