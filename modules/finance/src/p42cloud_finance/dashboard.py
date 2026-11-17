"""The cost dashboard: results per profit center, costs by type, center and month, CAPEX, and
unpaid invoices. Lists sum what they can; the result (income less costs) needs a route."""

from decimal import Decimal as Exact
from typing import Any

from p42cloud.sdk import Client, Route, RouteRequest, Widget

THIS_YEAR = {"field": "date", "op": "gte", "value": "@start_of_year"}
COSTS = {"field": "kind", "op": "eq", "value": "cost"}
RUNNING = {"field": "capex", "op": "eq", "value": False}


async def _results(request: RouteRequest) -> dict[str, Any]:
    """Income less costs this year, per profit center (in the main currency; running costs
    only: what adds value is in the assets)."""
    client = Client.of(request)
    found: dict[str, Exact] = {}
    names: dict[str, str] = {}
    after: str | None = None
    while True:
        page = await client.list(
            "finance.booking", where={"and": [THIS_YEAR, RUNNING]}, limit=500, after=after
        )
        for booking in page["records"]:
            center = booking.get("profit_center")
            if center is None or booking.get("amount_main") is None:
                continue
            sign = 1 if booking["kind"] == "income" else -1
            found[center] = found.get(center, Exact(0)) + sign * Exact(str(booking["amount_main"]))
        after = page["next"]
        if not after:
            break
    for center in found:
        names[center] = str((await client.get("costing.center", center))["name"])
    main = str(await client.setting("sys.main_currency") or "CHF")
    return {
        "subtitle": f"Income less running costs this year, in {main}",
        "points": [
            {"key": k, "label": names[k], "value": float(v)}
            for k, v in sorted(found.items(), key=lambda kv: names[kv[0]])
        ],
    }


ROUTES = (Route("GET", "/widget/results", _results, "Results per profit center this year."),)

WIDGETS = (
    Widget(
        "results",
        "Result by profit center",
        "chart",
        dashboard="finance",
        list="finance.booking",
        source="/widget/results",
        chart="bar",
        icon="balance",
        sizes=("wide", "large"),
        size="wide",
    ),
    Widget(
        "costs_by_type",
        "Costs by cost type",
        "chart",
        dashboard="finance",
        list="finance.booking",
        filter={"and": [THIS_YEAR, COSTS, RUNNING]},
        measure="sum",
        field="amount_main",
        group="cost_type",
        chart="donut",
        icon="category",
    ),
    Widget(
        "costs_by_center",
        "Costs by cost center",
        "chart",
        dashboard="finance",
        list="finance.booking",
        filter={"and": [THIS_YEAR, COSTS, RUNNING]},
        measure="sum",
        field="amount_main",
        group="center",
        chart="bar",
        icon="account_tree",
    ),
    Widget(
        "costs_by_month",
        "Costs by month",
        "chart",
        dashboard="finance",
        list="finance.booking",
        filter={"and": [{"field": "date", "op": "gte", "value": "@today-12m"}, COSTS, RUNNING]},
        measure="sum",
        field="amount_main",
        group="date",
        period="month",
        chart="column",
        icon="bar_chart",
        sizes=("wide", "large"),
        size="wide",
    ),
    Widget(
        "capex",
        "Adding value this year",
        "figure",
        dashboard="finance",
        list="finance.booking",
        filter={"and": [THIS_YEAR, {"field": "capex", "op": "eq", "value": True}]},
        measure="sum",
        field="amount_main",
        icon="construction",
    ),
    Widget(
        "unpaid",
        "Unpaid invoices",
        "figure",
        dashboard="finance",
        list="finance.booking",
        filter={"and": [COSTS, {"field": "paid", "op": "empty"}]},
        icon="pending_actions",
    ),
)
