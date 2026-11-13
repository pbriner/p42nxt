"""List views (declared by modules), saved views and remembered state (each person's own)."""

from dataclasses import dataclass
from typing import Any

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

LIST_VIEW = Table(
    "list_view",
    "List view",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "table": String("Table", max_length=80, required=True),
        "label": TranslatedText("Label", required=True),
        "columns": Json("Columns", required=True),
        "sort": Json("Sort"),
        "filter": Json("Filter"),
        # Further columns people may add; empty: every field they may read.
        "available": Json("More columns"),
        "search": Json("Searched fields"),
        "searchable": Boolean("In the search", required=True, default=True),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="List views",
    display="label",
)

SAVED_VIEW = Table(
    "saved_view",
    "Saved view",
    {
        "person": Reference("Person", "sys.person", required=True),
        "view": String("List view", max_length=120, required=True),
        "name": String("Name", max_length=100, required=True),
        "columns": Json("Columns"),
        "sort": Json("Sort"),
        "filter": Json("Filter"),
        "search": String("Search", max_length=200),
        "is_default": Boolean("Opens by default", required=True, default=False),
    },
    label_plural="Saved views",
    owner="person",
    display="name",
)

# The columns and sort a person last used on a list without a saved view.
VIEW_STATE = Table(
    "view_state",
    "Remembered list",
    {
        "person": Reference("Person", "sys.person", required=True),
        "view": String("List view", max_length=120, required=True),
        "columns": Json("Columns"),
        "sort": Json("Sort"),
    },
    label_plural="Remembered lists",
    owner="person",
    unique_together=(("person", "view"),),
)

TABLES = [LIST_VIEW, SAVED_VIEW, VIEW_STATE]


@dataclass(frozen=True)
class ListView:
    """What a module shows of one of its tables in a list.

    Its name is the table's (`task.task`): the table's default view. A table without a declared
    view gets one made from its fields.
    """

    table: str  # <module>.<table>
    columns: tuple[str, ...]
    label: str | dict[str, str] | None = None  # None: the table's plural label
    sort: tuple[str, ...] = ("-updated",)
    filter: Any = None
    available: tuple[str, ...] = ()
    search: tuple[str, ...] = ()  # empty: the text fields among the columns
    searchable: bool = True  # offered in the search across lists
