"""Widgets (declared by modules), who gets them, and each person's dashboards."""

from dataclasses import dataclass
from typing import Any, Literal

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.icon import Icon
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

SHOWN = {"available": "Available", "default": "Shown by default", "mandatory": "Always shown"}

WIDGET = Table(
    "widget",
    "Widget",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "title": TranslatedText("Title", required=True),
        "kind": Choice(
            "Kind",
            {"list": "List", "figure": "Figure", "chart": "Chart", "text": "Text"},
            required=True,
        ),
        # "home", or a module's ID: its dashboard.
        "dashboard": String("Dashboard", max_length=16, required=True),
        "list": String("List view name", max_length=120),
        "list_ref": Reference("List view", "sys.list_view", key="list"),
        # A GET route of the module answering the figure or the chart's points.
        "source_route": String("Source route", max_length=160),
        # A source widget about one record: the page offers a choice of the table's records.
        "param": String("Chosen", max_length=40),
        "param_table": String("Chosen from", max_length=120),
        "filter": Json("Filter"),
        "measure": Choice(
            "Measure",
            {"count": "Count", "sum": "Sum", "avg": "Average", "min": "Smallest", "max": "Largest"},
            required=True,
            default="count",
        ),
        "field": String("Measured field", max_length=63),
        "group": String("Grouped by", max_length=63),
        "period": Choice("Per", {"day": "Day", "week": "Week", "month": "Month", "year": "Year"}),
        "chart": Choice(
            "Chart", {"bar": "Bars", "donut": "Donut", "column": "Columns", "line": "Line"}
        ),
        "text": TranslatedText("Text"),
        "icon": Icon("Icon"),
        "accent": Integer("Accent (chart colour 1 to 8)"),
        "filled": Boolean("Filled", required=True, default=False),
        "target": Decimal("Target"),
        # A date field: a 12-month sparkline under the figure.
        "trend": String("Trend by", max_length=63),
        "sizes": Json("Sizes", required=True),
        "size": Choice("Size", {"s": "Small", "wide": "Wide", "large": "Large"}, required=True),
        "refresh": Integer("Refresh (seconds)"),
        "shown": Choice("For everyone who may read it", SHOWN, required=True, default="default"),
        "position": Integer("Position", required=True, default=0),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Widgets",
    display="title",
)

WIDGET_AUDIENCE = Table(
    "widget_audience",
    "Widget audience",
    {
        "widget": String("Widget name", max_length=120, required=True),
        "widget_ref": Reference("Widget", "sys.widget", key="widget"),
        "group": Reference("Group", "sys.group", required=True),
        "shown": Choice("Shown", SHOWN, required=True, default="default"),
    },
    label_plural="Widget audiences",
    unique_together=(("widget", "group"),),
)

DASHBOARD_LAYOUT = Table(
    "dashboard_layout",
    "Dashboard layout",
    {
        "person": Reference("Person", "sys.person", required=True),
        "dashboard": String("Dashboard", max_length=16, required=True),
        # [{"widget": name, "size": "s" | "wide" | "large"}], in order.
        "items": Json("Widgets", required=True),
        # The widgets the person has seen offered: the others are new to them.
        "known": Json("Known widgets"),
    },
    label_plural="Dashboard layouts",
    owner="person",
    unique_together=(("person", "dashboard"),),
)

TABLES = [WIDGET, WIDGET_AUDIENCE, DASHBOARD_LAYOUT]
SIZES = ("s", "wide", "large")
HOME = "home"


@dataclass(frozen=True)
class Widget:
    """One widget of the catalog. Its data comes from a list view, so each person sees only
    what they may read; a text widget needs none."""

    name: str  # unique in the module; the record is named <module>.<name>
    title: str | dict[str, str]
    kind: Literal["list", "figure", "chart", "text"]
    dashboard: str = HOME  # or the module's own ID
    list: str | None = None
    # For what a list can't count or sum: a GET route of the module (/widget/profit) answers
    # the figure or the points. The list still decides who may see the widget.
    source: str | None = None
    # A source widget about one record the person picks (a plan): its name in the query and the
    # table it's picked from, `("plan", "dca.plan")`. The page offers the choice.
    param: tuple[str, str] | None = None
    filter: Any = None
    measure: Literal["count", "sum", "avg", "min", "max"] = "count"
    field: str | None = None
    group: str | None = None
    period: Literal["day", "week", "month", "year"] | None = None
    chart: Literal["bar", "donut", "column", "line"] | None = None
    text: str | dict[str, str] | None = None
    icon: str | None = None
    accent: int | None = None
    filled: bool = False
    target: float | None = None
    trend: str | None = None
    sizes: tuple[str, ...] = SIZES
    size: str = "s"
    refresh: int | None = None
    shown: Literal["available", "default", "mandatory"] = "default"
