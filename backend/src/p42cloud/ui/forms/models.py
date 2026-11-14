"""Form views as records, and how modules declare them."""

from dataclasses import dataclass

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.json import Json
from p42cloud.data.types.string import String

FORM_VIEW = Table(
    "form_view",
    "Form view",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "table": String("Table", max_length=80, required=True),
        # [{"title": {...}, "fields": [...], "columns": 2}]
        "sections": Json("Sections", required=True),
        # [{"table": "task.task", "field": "project"}]: shown first, even when empty.
        "related": Json("Related lists"),
        # Also every other table pointing to the record (that the person may read).
        "related_auto": Boolean("Every related list", required=True, default=True),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Form views",
)

MAX_COLUMNS = 4


@dataclass(frozen=True)
class FormSection:
    title: str | dict[str, str]
    fields: tuple[str, ...]
    columns: int = 2


@dataclass(frozen=True)
class RelatedList:
    """Records of another table pointing to this one through `field`."""

    table: str
    field: str


@dataclass(frozen=True)
class FormView:
    """How a record of one of the module's tables is shown; named after the table."""

    table: str
    sections: tuple[FormSection, ...]
    related: tuple[RelatedList, ...] = ()
    related_auto: bool = True
