"""Costing: the structure every amount is booked against, for a household or a company.

Four questions per amount: whose result (a profit center), where the cost arose (a cost
center), what kind of cost (a cost type), and for which piece of work (an order). Profit and cost
centers form one tree; a cost center knows the profit center it belongs to, never typed in.
Shared cost centers (heating, IT) are spread over the centers they serve by a key (m², flats,
people). Bookings live in Finance, plans in Budget; both point here.
"""

from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    Boolean,
    Change,
    Choice,
    Client,
    Date,
    Decimal,
    FieldError,
    Flow,
    FormSection,
    FormView,
    Grant,
    Integer,
    Item,
    ListView,
    Module,
    Problem,
    Reference,
    RelatedList,
    Role,
    Section,
    String,
    Table,
    TableHook,
    Text,
    Transition,
    package_version,
)

ALL = ("read", "create", "write", "delete", "restore")

CENTER = Table(
    "center",
    "Center",
    {
        "code": String("Code", max_length=20, required=True, unique=True),
        "name": String("Name", max_length=120, required=True),
        "kind": Choice(
            "Kind",
            {"profit": "Profit center", "cost": "Cost center"},
            required=True,
            default="cost",
            tones={"profit": "primary"},
        ),
        "parent": Reference("Part of", "costing.center"),
        # The profit center whose result its costs count for: itself, or the nearest above it.
        "profit_center": Reference("Profit center", "costing.center", read_only=True),
        "responsible": Reference("Responsible", "sys.person"),
        "shared": Boolean("Shared: spread over others", required=True, default=False),
        "active": Boolean("Active", required=True, default=True),
        "description": Text("Description"),
    },
    label_plural="Centers",
    display="name",
    parent="parent",
    comments=True,
)

COST_TYPE = Table(
    "cost_type",
    "Cost type",
    {
        "code": String("Code", max_length=20, required=True, unique=True),
        "name": String("Name", max_length=120, required=True),
        "group": String("Group", max_length=60),
        "kind": Choice("Kind", {"cost": "Cost", "income": "Income"}, required=True, default="cost"),
        "chargeable": Boolean("May be charged on", required=True, default=False),
        "active": Boolean("Active", required=True, default=True),
    },
    label_plural="Cost types",
    display="name",
)

ORDER = Table(
    "order",
    "Order",
    {
        "code": String("Code", max_length=20, required=True, unique=True),
        "name": String("Name", max_length=120, required=True),
        "kind": Choice(
            "Kind",
            {"capex": "Adds value (CAPEX)", "opex": "Upkeep or expense (OPEX)"},
            required=True,
            default="opex",
        ),
        "center": Reference("Center", "costing.center", required=True, on_delete="restrict"),
        "state": Choice(
            "State",
            {"open": "Open", "done": "Done", "settled": "Settled"},
            required=True,
            default="open",
            tones={"done": "tertiary", "settled": "primary"},
        ),
        "start": Date("Start"),
        "end": Date("End"),
        "responsible": Reference("Responsible", "sys.person"),
        # Adding value: written off over these years once done.
        "useful_life": Integer("Written off over (years)"),
        "description": Text("Description"),
    },
    label_plural="Orders",
    display="name",
    files=True,
    comments=True,
)

KEY = Table(
    "key",
    "Spreading key",
    {
        "name": String("Name", max_length=120, required=True),
        "center": Reference("Spreads", "costing.center", required=True, on_delete="restrict"),
        "unit": Choice(
            "By",
            {
                "m2": "m²",
                "units": "Units",
                "people": "People",
                "percent": "Percent",
                "other": "Other",
            },
            required=True,
            default="units",
        ),
        "active": Boolean("Active", required=True, default=True),
    },
    label_plural="Spreading keys",
    display="name",
)

KEY_SHARE = Table(
    "key_share",
    "Share",
    {
        "key": Reference("Key", "costing.key", required=True, on_delete="cascade"),
        "center": Reference("To", "costing.center", required=True, on_delete="restrict"),
        "value": Decimal("Value", precision=18, scale=4, required=True),
    },
    label_plural="Shares",
    unique_together=(("key", "center"),),
    display="center",
)

TABLES = (CENTER, COST_TYPE, ORDER, KEY, KEY_SHARE)

CONTROLLER = Role(
    "costing.controller",
    "Controller",
    grants=tuple(Grant(f"costing.{t.name}", ALL) for t in TABLES),
    description="Sets up the centers, cost types, orders and keys.",
)
READER = Role(
    "costing.reader",
    "Costing reader",
    grants=tuple(Grant(f"costing.{t.name}", ("read",)) for t in TABLES),
    description="Reads the cost structure: to book and plan against it.",
)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _current(change: Change, table: str) -> dict[str, Any]:
    if change.operation == "write" and change.sys_id is not None:
        return await Client.of(change).get(table, change.sys_id)
    return {}


async def _check_center(change: Change) -> None:
    """A profit center sits at the top or under another profit center; a shared center is a
    cost center. Each center learns its profit center from where it stands."""
    if change.operation in ("delete", "restore"):
        return
    merged = await _current(change, "costing.center") | change.values
    kind, parent = merged.get("kind", "cost"), merged.get("parent")
    above = await Client.of(change).get("costing.center", str(parent)) if parent else None
    if kind == "profit" and above is not None and above["kind"] != "profit":
        raise _invalid("parent", "A profit center is at the top or under another profit center.")
    if kind == "profit" and merged.get("shared"):
        raise _invalid("shared", "Only a cost center is shared.")
    if kind == "profit":
        found = change.sys_id  # itself (a new one: set right after it's made)
    elif above is None:
        found = None
    else:
        found = above["sys_id"] if above["kind"] == "profit" else above.get("profit_center")
    if found is not None or kind != "profit":
        change.values["profit_center"] = str(found) if found else None


async def _after_center(change: Change) -> None:
    """A new profit center is its own; moving a center or changing its kind passes the profit
    center on to everything under it, level by level."""
    client = Client.of(change)
    if change.operation == "create":
        made = await client.list(
            "costing.center",
            where={"field": "code", "op": "eq", "value": change.values.get("code")},
            limit=1,
        )
        record = made["records"][0]
        if record["kind"] == "profit" and record.get("profit_center") is None:
            await client.update(
                "costing.center",
                record["sys_id"],
                {"profit_center": record["sys_id"]},
                record["_etag"],
            )
        return
    if change.operation != "write" or not {"parent", "kind"} & set(change.values):
        return
    under = await client.list(
        "costing.center",
        where={"field": "parent", "op": "eq", "value": str(change.sys_id)},
        limit=500,
    )
    for child in under["records"]:
        # Saving its parent again recomputes its profit center, and passes it on below it.
        await client.update(
            "costing.center", child["sys_id"], {"parent": child["parent"]}, child["_etag"]
        )


async def _check_order(change: Change) -> None:
    """An order adding value says over how many years it's written off."""
    if change.operation in ("delete", "restore"):
        return
    merged = await _current(change, "costing.order") | change.values
    if merged.get("kind") == "capex" and not merged.get("useful_life"):
        raise _invalid("useful_life", "An order adding value is written off over some years.")
    life = merged.get("useful_life")
    if life is not None and not 1 <= int(life) <= 100:  # noqa: PLR2004
        raise _invalid("useful_life", "From 1 to 100 years.")
    start, end = merged.get("start"), merged.get("end")
    if start and end and str(end) < str(start):
        raise _invalid("end", "The end comes after the start.")


async def _check_key(change: Change) -> None:
    """A key spreads a shared cost center."""
    if change.operation in ("delete", "restore"):
        return
    merged = await _current(change, "costing.key") | change.values
    center = await Client.of(change).get("costing.center", str(merged["center"]))
    if not center["shared"]:
        raise _invalid("center", "Mark the center as shared first: a key spreads a shared one.")


async def _check_share(change: Change) -> None:
    """A share goes to another cost center than the one spread, and isn't negative."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    merged = await _current(change, "costing.key_share") | change.values
    key = await client.get("costing.key", str(merged["key"]))
    if str(merged["center"]) == str(key["center"]):
        raise _invalid("center", "A center isn't spread over itself.")
    receiver = await client.get("costing.center", str(merged["center"]))
    if receiver["kind"] != "cost":
        raise _invalid("center", "Shares go to cost centers.")
    if float(str(merged["value"])) < 0:
        raise _invalid("value", "A share isn't negative.")


module = Module(
    id="costing",
    name="Costing",
    publisher="p42",
    version=package_version("p42cloud-costing"),
    depends_on={"sys": ">=0.46,<1"},
    tables=TABLES,
    roles=(CONTROLLER, READER),
    hooks=(
        TableHook("costing.center", before=_check_center, after=_after_center),
        TableHook("costing.order", before=_check_order),
        TableHook("costing.key", before=_check_key),
        TableHook("costing.key_share", before=_check_share),
    ),
    flows=(
        Flow(
            "costing.order",
            "state",
            initial="open",
            transitions=(
                Transition("finish", "Mark done", "open", "done", icon="task_alt"),
                Transition("reopen", "Reopen", "done", "open", icon="undo"),
                Transition(
                    "settle",
                    "Settle",
                    "done",
                    "settled",
                    roles=("costing.controller",),
                    icon="verified",
                    confirm="Settle the order? Its costs are then final.",
                ),
            ),
        ),
    ),
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Profit and cost centers, cost types, orders and spreading keys.",
    category="Finance",
    icon="account_tree",
    views=(
        ListView(
            "costing.center",
            ("code", "name", "kind", "profit_center", "responsible", "shared", "active"),
            sort=("code",),
            search=("code", "name"),
        ),
        ListView(
            "costing.cost_type",
            ("code", "name", "group", "kind", "chargeable", "active"),
            sort=("code",),
            search=("code", "name"),
        ),
        ListView(
            "costing.order",
            ("code", "name", "kind", "center", "state", "end", "responsible"),
            sort=("code",),
            search=("code", "name"),
        ),
        ListView("costing.key", ("name", "center", "unit", "active"), sort=("name",)),
        ListView("costing.key_share", ("key", "center", "value"), sort=("key",), searchable=False),
    ),
    forms=(
        FormView(
            "costing.center",
            (
                FormSection("Center", ("code", "name", "kind", "parent", "profit_center")),
                FormSection("Responsibility", ("responsible", "shared", "active", "description")),
            ),
            related=(
                RelatedList("costing.center", "parent"),
                RelatedList("costing.order", "center"),
                RelatedList("costing.key", "center"),
            ),
        ),
        FormView(
            "costing.cost_type",
            (FormSection("Cost type", ("code", "name", "group", "kind", "chargeable", "active")),),
        ),
        FormView(
            "costing.order",
            (
                FormSection("Order", ("code", "name", "kind", "center", "state")),
                FormSection(
                    "When and who", ("start", "end", "useful_life", "responsible", "description")
                ),
            ),
        ),
        FormView(
            "costing.key",
            (FormSection("Spreading key", ("name", "center", "unit", "active")),),
            related=(RelatedList("costing.key_share", "key"),),
        ),
    ),
    navigation=(
        Section(
            "Cost structure",
            (
                Item("centers", "Centers", "costing.center", icon="account_tree"),
                Item("cost_types", "Cost types", "costing.cost_type", icon="category"),
                Item("orders", "Orders", "costing.order", icon="construction"),
                Item("keys", "Spreading keys", "costing.key", icon="pie_chart"),
            ),
        ),
    ),
)
