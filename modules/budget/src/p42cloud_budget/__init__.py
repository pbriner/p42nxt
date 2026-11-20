"""Budget: what's planned per cost center and cost type, with a rhythm that fills the months, and
how it compares to what was spent.

A version (the budget for a year, a forecast) is drafted, submitted, approved and locked; its
lines only change while it's a draft. Actual costs come from Finance's bookings when Finance is
installed: Budget works without it, and then shows plans only.
"""

from contextvars import ContextVar
from decimal import Decimal as Exact
from pathlib import Path
from typing import Any

from p42cloud.sdk import (
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
    Migration,
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
    Widget,
    package_version,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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

RHYTHMS = {
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "yearly": "Yearly, spread over the months",
    "once": "Once",
    "by_hand": "By hand",
}
LINE = Table(
    "plan_line",
    "Budget line",
    {
        "version": Reference("Version", "budget.version", required=True, on_delete="cascade"),
        "center": Reference("Cost center", "costing.center", required=True, on_delete="restrict"),
        "cost_type": Reference(
            "Cost type", "costing.cost_type", required=True, on_delete="restrict"
        ),
        "rhythm": Choice("Rhythm", RHYTHMS, required=True, default="monthly"),
        "currency": Currency("Currency", read_only=True),
        # Each time (monthly, quarterly, once), or for the whole year (yearly); not by hand.
        "amount": Money("Amount", currency_field="currency"),
        "from_month": Integer("From month", required=True, default=1),
        "year_total": Money("Year total", currency_field="currency", read_only=True),
        "note": String("Note", max_length=200),
    },
    label_plural="Budget lines",
    unique_together=(("version", "center", "cost_type"),),
    display="note",
)

MONTH = Table(
    "month",
    "Budget month",
    {
        "line": Reference("Budget line", "budget.plan_line", required=True, on_delete="cascade"),
        "month": Integer("Month", required=True),
        "currency": Currency("Currency", read_only=True),
        "amount": Money("Amount", currency_field="currency", required=True),
    },
    label_plural="Budget months",
    unique_together=(("line", "month"),),
)

TABLES = (VERSION, LINE, MONTH)
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


# Set while Budget's own hooks write months and totals: what people may not write directly.
_OWN_WRITES: ContextVar[bool] = ContextVar("budget_own_writes", default=False)
CENT = Exact("0.01")
RHYTHM_FIELDS = frozenset({"rhythm", "amount", "from_month"})


async def _check_version(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    year = change.values.get("year")
    if year is not None and not 2000 <= int(year) <= 2100:  # noqa: PLR2004
        raise _invalid("year", "A year from 2000 to 2100.")


def months_of(rhythm: str, amount: Exact, start: int) -> dict[int, Exact]:
    """The months a rhythm fills, with their amounts; by hand fills none."""
    if rhythm == "monthly":
        return dict.fromkeys(range(start, 13), amount)
    if rhythm == "quarterly":
        return dict.fromkeys(range(start, 13, 3), amount)
    if rhythm == "once":
        return {start: amount}
    if rhythm == "yearly":  # evenly, the cents left over in the last month
        months = list(range(start, 13))
        each = (amount / len(months)).quantize(CENT)
        spread = dict.fromkeys(months, each)
        spread[12] = amount - each * (len(months) - 1)
        return spread
    return {}


async def _months(client: Client, line: str) -> list[dict[str, Any]]:
    found = await client.list(
        "budget.month", where={"field": "line", "op": "eq", "value": line}, limit=50
    )
    return found["records"]


async def _draft(client: Client, version: str) -> dict[str, Any]:
    found = await client.get("budget.version", version)
    if found["state"] != "draft":
        raise _invalid("version", "Only a draft version's lines change.")
    return found


async def _check_line(change: Change) -> None:
    """A line belongs to a draft version and a cost center; it takes its version's currency, and
    its year total follows from its rhythm (by hand: from its months)."""
    client = Client.of(change)
    current = (
        await client.get("budget.plan_line", change.sys_id)
        if change.operation != "create" and change.sys_id is not None
        else {}
    )
    merged = current | change.values
    version = await _draft(client, str(merged["version"]))
    if change.operation in ("delete", "restore"):
        return
    if "version" in change.values and current and str(current["version"]) != str(merged["version"]):
        raise _invalid("version", "A line stays in its version.")
    if "year_total" in change.sent and not _OWN_WRITES.get():
        raise _invalid("year_total", "Worked out from the months.")
    start = int(merged["from_month"])
    if not 1 <= start <= 12:  # noqa: PLR2004
        raise _invalid("from_month", "A month from 1 to 12.")
    center = await client.get("costing.center", str(merged["center"]))
    if center["kind"] != "cost":
        raise _invalid("center", "Plan on cost centers.")
    change.values["currency"] = version["currency"]
    if change.operation == "create" or set(change.values) & RHYTHM_FIELDS:
        change.values["year_total"] = await _total(client, merged, change.sys_id)


async def _total(client: Client, line: dict[str, Any], sys_id: Any) -> Exact:
    if line["rhythm"] == "by_hand":
        months = await _months(client, str(sys_id)) if sys_id is not None else []
        return sum((Exact(str(m["amount"])) for m in months), Exact(0))
    if line.get("amount") is None:
        raise _invalid("amount", "How much, each time or for the year.")
    amount = Exact(str(line["amount"]).replace("'", "")).quantize(CENT)
    return sum(months_of(line["rhythm"], amount, int(line["from_month"])).values(), Exact(0))


async def _fill_months(change: Change) -> None:
    """After a line is saved: its months as its rhythm says (by hand: left as they are)."""
    if change.operation not in ("create", "write") or change.sys_id is None:
        return
    if change.operation == "write" and not set(change.values) & RHYTHM_FIELDS:
        return
    client = Client.of(change)
    line = await client.get("budget.plan_line", change.sys_id)
    if line["rhythm"] == "by_hand":
        return
    amount = Exact(str(line["amount"])).quantize(CENT)
    wanted = months_of(line["rhythm"], amount, int(line["from_month"]))
    token = _OWN_WRITES.set(True)
    try:
        for month in await _months(client, str(change.sys_id)):
            number = int(month["month"])
            if number not in wanted:
                await client.delete("budget.month", month["sys_id"], month["_etag"])
                continue
            value = wanted.pop(number)
            if Exact(str(month["amount"])) != value:
                await client.update(
                    "budget.month", month["sys_id"], {"amount": str(value)}, month["_etag"]
                )
        for number, value in sorted(wanted.items()):
            await client.create(
                "budget.month",
                {"line": str(change.sys_id), "month": number, "amount": str(value)},
            )
    finally:
        _OWN_WRITES.reset(token)


async def _check_month(change: Change) -> None:
    """A month is typed only on a line planned by hand; the others follow their line."""
    client = Client.of(change)
    current = (
        await client.get("budget.month", change.sys_id)
        if change.operation != "create" and change.sys_id is not None
        else {}
    )
    merged = current | change.values
    line = await client.get("budget.plan_line", str(merged["line"]))
    version = await _draft(client, str(line["version"]))
    if change.operation in ("delete", "restore"):
        return
    if "line" in change.values and current and str(current["line"]) != str(merged["line"]):
        raise _invalid("line", "A month stays with its line.")
    if line["rhythm"] != "by_hand" and not _OWN_WRITES.get():
        raise _invalid("amount", "Set by the line's rhythm: choose By hand to type the months.")
    month = int(merged["month"])
    if not 1 <= month <= 12:  # noqa: PLR2004
        raise _invalid("month", "A month from 1 to 12.")
    change.values["currency"] = version["currency"]


async def _add_up(change: Change) -> None:
    """After a month typed by hand changes: its line's year total."""
    if _OWN_WRITES.get() or change.sys_id is None:
        return
    client = Client.of(change)
    month = await client.get("budget.month", change.sys_id)
    line = await client.get("budget.plan_line", str(month["line"]))
    if line.get("deleted"):  # restored or deleted together with its line
        return
    total = sum(
        (Exact(str(m["amount"])) for m in await _months(client, str(line["sys_id"]))), Exact(0)
    )
    token = _OWN_WRITES.set(True)
    try:
        await client.update(
            "budget.plan_line", line["sys_id"], {"year_total": str(total)}, line["_etag"]
        )
    finally:
        _OWN_WRITES.reset(token)


async def move_lines(conn: AsyncConnection) -> None:
    """0.1 planned one line per month: each cost center and cost type of a version becomes one
    line, monthly when its twelve months are equal, else by hand with the months kept. The 0.1
    lines stay where they were (retired with their table)."""
    rows = (
        await conn.execute(
            text(
                "SELECT version, center, cost_type, month, amount, currency, note"
                " FROM budget.line WHERE deleted IS NULL ORDER BY version, center, cost_type, month"
            )
        )
    ).all()
    groups: dict[tuple[Any, Any, Any], list[Any]] = {}
    for row in rows:
        groups.setdefault((row.version, row.center, row.cost_type), []).append(row)
    for (version, center, cost_type), months in groups.items():
        amounts = {int(r.month): Exact(r.amount) for r in months}
        even = len(amounts) == 12 and len(set(amounts.values())) == 1  # noqa: PLR2004
        notes = "; ".join(dict.fromkeys(r.note for r in months if r.note))[:200] or None
        line = (
            await conn.execute(
                text(
                    "INSERT INTO budget.plan_line (version, center, cost_type, rhythm, amount,"
                    " from_month, currency, year_total, note) VALUES (:version, :center,"
                    " :cost_type, :rhythm, :amount, 1, :currency, :total, :note) RETURNING sys_id"
                ),
                {
                    "version": version,
                    "center": center,
                    "cost_type": cost_type,
                    "rhythm": "monthly" if even else "by_hand",
                    "amount": amounts[1] if even else None,
                    "currency": months[0].currency,
                    "total": sum(amounts.values(), Exact(0)),
                    "note": notes,
                },
            )
        ).scalar_one()
        for month, amount in amounts.items():
            await conn.execute(
                text(
                    "INSERT INTO budget.month (line, month, currency, amount)"
                    " VALUES (:line, :month, :currency, :amount)"
                ),
                {"line": line, "month": month, "currency": months[0].currency, "amount": amount},
            )


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
        client, "budget.plan_line", {"field": "version", "op": "eq", "value": version_id}
    ):
        planned[str(line["center"])] = planned.get(str(line["center"]), Exact(0)) + Exact(
            str(line["year_total"] or 0)
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
    depends_on={"sys": ">=0.46,<1", "costing": ">=0.3,<1"},
    tables=TABLES,
    roles=(PLANNER, CONTROLLER, READER),
    hooks=(
        TableHook("budget.version", before=_check_version),
        TableHook("budget.plan_line", before=_check_line, after=_fill_months),
        TableHook("budget.month", before=_check_month, after=_add_up),
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
    migrations=(Migration("0.2.0", move_lines),),
    routes=ROUTES,
    widgets=WIDGETS,
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Budget versions and their lines per cost center and cost type, with a rhythm.",
    category="Finance",
    icon="savings",
    views=(
        ListView(
            "budget.version", ("name", "year", "kind", "state", "currency"), sort=("-year", "name")
        ),
        ListView(
            "budget.plan_line",
            ("version", "center", "cost_type", "rhythm", "amount", "from_month", "year_total"),
            sort=("version", "center", "cost_type"),
            searchable=False,
        ),
        ListView(
            "budget.month",
            ("line", "month", "amount"),
            sort=("line", "month"),
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
            related=(RelatedList("budget.plan_line", "version"),),
        ),
        FormView(
            "budget.plan_line",
            (
                FormSection("Budget line", ("version", "center", "cost_type", "note")),
                FormSection("Amount", ("rhythm", "amount", "from_month", "currency", "year_total")),
            ),
            related=(RelatedList("budget.month", "line"),),
        ),
        FormView(
            "budget.month",
            (FormSection("Budget month", ("line", "month", "amount", "currency")),),
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
                Item("lines", "Budget lines", "budget.plan_line", icon="table_rows"),
            ),
        ),
    ),
)
