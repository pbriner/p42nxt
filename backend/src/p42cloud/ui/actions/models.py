"""UI actions as records, and how modules declare them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from p42cloud.data.declarations import Table
from p42cloud.data.records import Records
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.icon import Icon
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

UI_ACTION = Table(
    "ui_action",
    "UI action",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "table": String("Table name", max_length=80, required=True),
        "table_ref": Reference("Table", "sys.table", key="table", match="schema.name"),
        "label": TranslatedText("Label", required=True),
        "icon": Icon("Icon"),
        # form_button, form_menu, list_bulk, list_row
        "places": Json("Where", required=True),
        "operation": Choice(
            "Needs",
            {"write": "Changing the record", "delete": "Deleting it", "read": "Reading it"},
            required=True,
            default="write",
        ),
        "condition": Json("When"),
        "confirm": TranslatedText("Ask first"),
        # [{"name", "label", "type", "required", "options"}]
        "inputs": Json("Asks for"),
        "second_factor": Boolean("Needs the second factor again", required=True, default=False),
        # {field: value or "@input.<name>"}: what it sets, without code.
        "set": Json("Sets"),
        "handler": Boolean("Runs the module's code", required=True, default=False),
        "message": TranslatedText("Says when done"),
        "go": Choice(
            "Then",
            {"stay": "Stay", "list": "Go to the list"},
            required=True,
            default="stay",
        ),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="UI actions",
    display="label",
)

PLACES = frozenset({"form_button", "form_menu", "list_bulk", "list_row"})
INPUT_TYPES = frozenset({"string", "text", "choice", "boolean", "date", "integer", "reference"})


@dataclass(frozen=True)
class ActionInput:
    """Something the action asks for before it runs."""

    name: str
    label: str
    type: str = "string"
    required: bool = True
    options: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class ActionRun:
    """One run of an action on one record, for its handler."""

    records: Records
    table: str
    sys_id: UUID
    record: dict[str, Any]  # as the person may read it
    inputs: dict[str, Any]

    @property
    def person(self) -> UUID:
        return self.records.situation.person


@dataclass(frozen=True)
class ActionResult:
    message: str | None = None
    go: Literal["stay", "list"] | None = None


type Handler = Callable[[ActionRun], Awaitable[ActionResult | None]]


@dataclass(frozen=True)
class UIAction:
    """A button or menu entry. Shown, and run, only where the person may do `operation` on the
    record and `condition` holds; either `handler` (the module's code) or `set` does the work."""

    name: str  # unique in the module; the record is named <module>.<name>
    table: str
    label: str | dict[str, str]
    places: tuple[str, ...] = ("form_button",)
    icon: str | None = None
    operation: Literal["write", "delete", "read"] = "write"
    condition: Any = None
    confirm: str | dict[str, str] | None = None
    inputs: tuple[ActionInput, ...] = ()
    second_factor: bool = False
    set: dict[str, Any] | None = None
    handler: Handler | None = None
    message: str | dict[str, str] | None = None
    go: Literal["stay", "list"] = "stay"
