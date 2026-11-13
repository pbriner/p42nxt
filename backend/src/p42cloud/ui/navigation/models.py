"""Navigation items: what a module puts into the navigator, as records."""

import re
from dataclasses import dataclass
from typing import Any, Literal

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.icon import Icon
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

NAV_ITEM = Table(
    "nav_item",
    "Navigation item",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "section": TranslatedText("Section"),
        "label": TranslatedText("Label", required=True),
        "icon": Icon("Icon"),
        "kind": Choice(
            "Opens",
            {"list": "A list", "create": "A new record", "page": "A page"},
            required=True,
            default="list",
        ),
        # Who may read the table (a list, a page) or make records in it (create) sees the item.
        "table": String("Table", max_length=80),
        "view": String("List view", max_length=120),
        "filter": Json("Filter"),
        "path": String("Page", max_length=200),
        "keywords": Json("Keywords"),
        "position": Integer("Position", required=True, default=0),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Navigation items",
    display="label",
)

ITEM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# An app path: no scheme, no host, nothing that leaves the app.
PAGE = re.compile(r"^/[a-z0-9][a-z0-9/_-]{0,198}$")


@dataclass(frozen=True)
class Item:
    """One entry of the navigator, opening a list, a new record's form or a page."""

    name: str  # unique in the module; the record's name is <module>.<name>
    label: str | dict[str, str]
    table: str | None = None  # <module>.<table>: who may read it (create: make records) sees it
    kind: Literal["list", "create", "page"] = "list"
    view: str | None = None  # a list view's name; None: the table's default view
    filter: Any = None  # the filter language, applied on top of the view's
    path: str | None = None  # a page of the app (kind page)
    icon: str | None = None
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class Section:
    """A heading over some of a module's items."""

    label: str | dict[str, str]
    items: tuple[Item, ...]


type Entry = Item | Section
