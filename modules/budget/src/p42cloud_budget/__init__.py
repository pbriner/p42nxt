"""Budget: what's planned per cost center, cost type and month, and how it compares to what was
spent.

A version (the budget for a year, a forecast) is drafted, submitted, approved and locked; its
lines only change while it's a draft. Actual costs come from Finance's bookings when Finance is
installed: Budget works without it, and then shows plans only.
"""

from decimal import Decimal as Exact
from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    ActionInput,
    ActionResult,
    ActionRun,
    Change,
    Choice,
    Client,
    Currency,
    FieldError,
    Flow,
    FormSection,
    FormView,
    Grant,
    Integer,
    Item,
    ListView,
    Module,
    Money,
    Problem,
    Reference,
    RelatedList,
    Role,
    Route,
    RouteRequest,
    Section,
    String,
    Table,
    TableHook,
    Text,
    Transition,
    UIAction,
    Widget,
    package_version,
)

ALL = ("read", "create", "write", "delete", "restore")

VERSION = Table(
    "version",
    "Budget version",
    {
        "name": String("Name", max_length=120, required=True),
        "year": Integer("Year", required=True),
        "kind": Choice(
            "Kind", {"budget": "Budget", "forecast": "Forecast"}, required=True, default="budget"
        ),
        "state": Choice(
            "State",
            {
                "draft": "Draft",
                "submitted": "Submitted",
                "approved": "Approved",
                "locked": "Locked",
            },
            required=True,
            default="draft",
            tones={"submitted": "tertiary", "approved": "primary", "locked": "primary"},
        ),
        "currency": Currency("Currency", required=True, default="CHF"),
        "note": Text("Note"),
    },
    label_plural="Budget versions",
    display="name",
    comments=True,
)

LINE = Table(
    "line",
    "Budget line",
    {
        "version": Reference("Version", "budget.version", required=True),
        "center": Reference("Cost center", "costing.center", required=True),
        "cost_type": Reference("Cost type", "costing.cost_type", required=True),
        "month": Integer("Month", required=True),
        "currency": Currency("Currency", read_only=True),
        "amount": Money("Amount", currency_field="currency", required=True),
        "note": String("Note", max_length=200),
    },
    label_plural="Budget lines",
    unique_together=(("version", "center", "cost_type", "month"),),
    display="note",
)

TABLES = (VERSION, LINE)
OWN = tuple(f"budget.{t.name}" for t in TABLES)

PLANNER = Role(
    "budget.planner",
    "Planner",
    grants=tuple(Grant(t, ALL) for t in OWN),
    includes=("costing.reader",),
    description="Drafts budget versions and their lines, and submits them.",
)
CONTROLLER = Role(
    "budget.controller",
    "Budget controller",
    grants=tuple(Grant(t, ALL) for t in OWN),
    includes=("costing.reader",),
    description="Approves, sends back and locks budget versions.",
)
READER = Role(
    "budget.reader",
    "Budget reader",
    grants=tuple(Grant(t, ("read",)) for t in OWN),
    includes=("costing.reader",),
    description="Reads budgets and how they compare to what was spent.",
)
CONTROLLERS = ("budget.controller",)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _check_version(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    year = change.values.get("year")
    if year is not None and not 2000 <= int(year) <= 2100:  # noqa: PLR2004
        raise _invalid("year", "A year from 2000 to 2100.")


async def _check_line(change: Change) -> None:
    """A line belongs to a draft version, a cost center and a month; it takes its version's
    currency."""
    client = Client.of(change)
    current = (
        await client.get("budget.line", change.sys_id)
        if change.operation != "create" and change.sys_id is not None
        else {}
    )
    merged = current | change.values
    version = await client.get("budget.version", str(merged["version"]))
    if version["state"] != "draft":
        raise _invalid("version", "Only a draft version's lines change.")
    if change.operation in ("delete", "restore"):
        return
    if "version" in change.values and current and str(current["version"]) != str(merged["version"]):
        raise _invalid("version", "A line stays in its version.")
    month = int(merged["month"])
    if not 1 <= month <= 12:  # noqa: PLR2004
        raise _invalid("month", "A month from 1 to 12.")
    center = await client.get("costing.center", str(merged["center"]))
    if center["kind"] != "cost":
        raise _invalid("center", "Plan on cost centers.")
    change.values["currency"] = version["currency"]


async def _by_code(client: Client, table: str, code: str, what: str) -> str:
    found = await client.list(
        table, where={"field": "code", "op": "eq", "value": code.strip()}, limit=1
    )
    if not found["records"]:
        raise Problem(404, "not_found", f"There's no {what} with the code {code.strip()!r}.")
    return str(found["records"][0]["sys_id"])


async def _spread(run: ActionRun) -> ActionResult:
    """A yearly amount for a cost center and cost type, spread evenly over the twelve months
    (the cents left over in December); months already planned are replaced."""
    client = Client(run.records)
    if run.record["state"] != "draft":
        raise Problem(409, "not_draft", "Only a draft version's lines change.")
    try:
        yearly = Exact(str(run.inputs["amount"]).replace("'", "")).quantize(Exact("0.01"))
    except ArithmeticError as error:
        raise Problem(422, "validation", "The amount is a number, like 12000.") from error
    center = await _by_code(client, "costing.center", str(run.inputs["center"]), "cost center")
    cost_type = await _by_code(
        client, "costing.cost_type", str(run.inputs["cost_type"]), "cost type"
    )
    monthly = (yearly / 12).quantize(Exact("0.01"))
    existing = await client.list(
        "budget.line",
        where={
            "and": [
                {"field": "version", "op": "eq", "value": str(run.sys_id)},
                {"field": "center", "op": "eq", "value": center},
                {"field": "cost_type", "op": "eq", "value": cost_type},
            ]
        },
        limit=50,
    )
    by_month = {int(r["month"]): r for r in existing["records"]}
    for month in range(1, 13):
        amount = monthly if month < 12 else yearly - monthly * 11  # noqa: PLR2004
        if month in by_month:
            line = by_month[month]
            await client.update(
                "budget.line", line["sys_id"], {"amount": str(amount)}, line["_etag"]
            )
        else:
            await client.create(
                "budget.line",
                {
                    "version": str(run.sys_id),
                    "center": center,
                    "cost_type": cost_type,
                    "month": month,
                    "amount": str(amount),
                },
            )
    return ActionResult(message=f"{yearly:,.2f} spread over the year: {monthly:,.2f} a month.")


async def _all(client: Client, table: str, where: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await client.list(table, where=where, limit=500, after=after)
        found += page["records"]
        after = page["next"]
        if not after:
            return found


async def compared(client: Client, version_id: str) -> list[dict[str, Any]]:
    """Per cost center: planned for the version's year, and spent (Finance's running costs in
    the main currency, if Finance is installed and readable; else nothing)."""
    version = await client.get("budget.version", version_id)
    year = int(version["year"])
    planned: dict[str, Exact] = {}
    for line in await _all(
        client, "budget.line", {"field": "version", "op": "eq", "value": version_id}
    ):
        planned[str(line["center"])] = planned.get(str(line["center"]), Exact(0)) + Exact(
            str(line["amount"])
        )
    spent: dict[str, Exact] = {}
    try:
        bookings = await _all(
            client,
            "finance.booking",
            {
                "and": [
                    {"field": "date", "op": "between", "value": [f"{year}-01-01", f"{year}-12-31"]},
                    {"field": "kind", "op": "eq", "value": "cost"},
                    {"field": "capex", "op": "eq", "value": False},
                ]
            },
        )
    except Problem:  # no Finance here, or not for this person: plans only
        bookings = []
    for booking in bookings:
        if booking.get("amount_main") is None or booking.get("center") is None:
            continue
        key = str(booking["center"])
        spent[key] = spent.get(key, Exact(0)) + Exact(str(booking["amount_main"]))
    rows: list[dict[str, Any]] = []
    for center in sorted(set(planned) | set(spent)):
        name = str((await client.get("costing.center", center))["name"])
        rows.append(
            {
                "center": center,
                "name": name,
                "planned": planned.get(center, Exact(0)),
                "spent": spent.get(center, Exact(0)),
            }
        )
    return sorted(rows, key=lambda r: r["name"])


async def _plan_vs_actual(request: RouteRequest) -> dict[str, Any]:
    client = Client.of(request)
    version = request.query.get("version")
    if not version:
        raise Problem(422, "validation", "Pick a budget version.")
    rows = await compared(client, version)
    return {
        "subtitle": "Share of the plan spent, per cost center (%)",
        "points": [
            {
                "key": r["center"],
                "label": r["name"],
                "value": round(float(r["spent"] / r["planned"] * 100), 1) if r["planned"] else 0.0,
            }
            for r in rows
        ],
    }


async def _used(request: RouteRequest) -> dict[str, Any]:
    client = Client.of(request)
    version = request.query.get("version")
    if not version:
        raise Problem(422, "validation", "Pick a budget version.")
    rows = await compared(client, version)
    planned = sum((r["planned"] for r in rows), Exact(0))
    spent = sum((r["spent"] for r in rows), Exact(0))
    share = float(spent / planned * 100) if planned else 0.0
    over = [r["name"] for r in rows if r["planned"] and r["spent"] > r["planned"]]
    return {
        "value": round(share, 1),
        "unit": "% used",
        "detail": f"Over budget: {', '.join(over)}."
        if over
        else "Every cost center within its plan.",
    }


ROUTES = (
    Route("GET", "/widget/plan_vs_actual", _plan_vs_actual, "Planned against spent per center."),
    Route("GET", "/widget/used", _used, "How much of a version is spent."),
)

WIDGETS = (
    Widget(
        "plan_vs_actual",
        "Plan used per cost center",
        "chart",
        dashboard="budget",
        list="budget.version",
        source="/widget/plan_vs_actual",
        param=("version", "budget.version"),
        chart="bar",
        icon="compare_arrows",
        sizes=("wide", "large"),
        size="wide",
    ),
    Widget(
        "used",
        "Budget used",
        "figure",
        dashboard="budget",
        list="budget.version",
        source="/widget/used",
        param=("version", "budget.version"),
        icon="data_usage",
    ),
)

module = Module(
    id="budget",
    name="Budget",
    publisher="p42",
    version=package_version("p42cloud-budget"),
    depends_on={"sys": ">=0.43,<1", "costing": ">=0.3,<1"},
    tables=TABLES,
    roles=(PLANNER, CONTROLLER, READER),
    hooks=(
        TableHook("budget.version", before=_check_version),
        TableHook("budget.line", before=_check_line),
    ),
    flows=(
        Flow(
            "budget.version",
            "state",
            initial="draft",
            transitions=(
                Transition("submit", "Submit", "draft", "submitted", icon="send"),
                Transition(
                    "send_back", "Send back", "submitted", "draft", roles=CONTROLLERS, icon="undo"
                ),
                Transition(
                    "approve",
                    "Approve",
                    "submitted",
                    "approved",
                    roles=CONTROLLERS,
                    icon="verified",
                ),
                Transition(
                    "lock",
                    "Lock",
                    "approved",
                    "locked",
                    roles=CONTROLLERS,
                    icon="lock",
                    confirm="Lock the version? It can't change any more.",
                ),
            ),
        ),
    ),
    actions=(
        UIAction(
            "spread",
            "budget.version",
            "Spread a year",
            places=("form_button",),
            icon="calendar_month",
            condition={"field": "state", "op": "eq", "value": "draft"},
            inputs=(
                ActionInput("center", "Cost center (code)", options={"max_length": 20}),
                ActionInput("cost_type", "Cost type (code)", options={"max_length": 20}),
                ActionInput("amount", "Amount for the year", options={"max_length": 20}),
            ),
            handler=_spread,
        ),
    ),
    routes=ROUTES,
    widgets=WIDGETS,
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Budget versions and their lines per cost center, cost type and month.",
    category="Finance",
    icon="savings",
    views=(
        ListView(
            "budget.version", ("name", "year", "kind", "state", "currency"), sort=("-year", "name")
        ),
        ListView(
            "budget.line",
            ("version", "center", "cost_type", "month", "amount", "note"),
            sort=("version", "center", "cost_type", "month"),
            searchable=False,
        ),
    ),
    forms=(
        FormView(
            "budget.version",
            (
                FormSection("Version", ("name", "year", "kind", "state", "currency")),
                FormSection("Note", ("note",), columns=1),
            ),
            related=(RelatedList("budget.line", "version"),),
        ),
        FormView(
            "budget.line",
            (
                FormSection(
                    "Budget line", ("version", "center", "cost_type", "month", "amount", "note")
                ),
            ),
        ),
    ),
    navigation=(
        Section(
            "Budget",
            (
                Item(
                    "dashboard",
                    "Dashboard",
                    kind="page",
                    path="/dashboard/budget",
                    icon="monitoring",
                ),
                Item("versions", "Budget versions", "budget.version", icon="savings"),
                Item("lines", "Budget lines", "budget.line", icon="table_rows"),
            ),
        ),
    ),
)
