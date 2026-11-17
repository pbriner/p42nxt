"""The cost dashboard: results per profit center, running costs and what adds value per profit
center, costs by type, center, month and supplier, investments by order, unpaid invoices. Lists
sum what they can; the result (income less costs) needs a route. The page offers a center: the
list widgets then show it and everything under it (a profit center with its cost centers)."""

from decimal import Decimal as Exact
from typing import Any

from p42cloud.sdk import Client, Route, RouteRequest, Widget

THIS_YEAR = {"field": "date", "op": "gte", "value": "@start_of_year"}
COSTS = {"field": "kind", "op": "eq", "value": "cost"}
RUNNING = {"field": "capex", "op": "eq", "value": False}
VALUE = {"field": "capex", "op": "eq", "value": True}
CENTER = ("center", "costing.center")


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
        "running_by_profit_center",
        "Running costs by profit center",
        "chart",
        dashboard="finance",
        list="finance.booking",
        param=CENTER,
        filter={"and": [COSTS, RUNNING]},
        measure="sum",
        field="amount_main",
        group="profit_center",
        chart="bar",
        dated="date",
        icon="receipt_long",
    ),
    Widget(
        "value_by_profit_center",
        "Adding value by profit center",
        "chart",
        dashboard="finance",
        list="finance.booking",
        param=CENTER,
        filter=VALUE,
        measure="sum",
        field="amount_main",
        group="profit_center",
        chart="bar",
        dated="date",
        accent=3,
        icon="construction",
    ),
    Widget(
        "value_by_order",
        "Adding value by order",
        "chart",
        dashboard="finance",
        list="finance.booking",
        param=CENTER,
        filter=VALUE,
        measure="sum",
        field="amount_main",
        group="order",
        chart="bar",
        dated="date",
        accent=3,
        icon="handyman",
        sizes=("s", "wide", "large"),
        size="wide",
    ),
    Widget(
        "costs_by_partner",
        "Costs by supplier",
        "chart",
        dashboard="finance",
        list="finance.booking",
        param=CENTER,
        filter=COSTS,
        measure="sum",
        field="amount_main",
        group="partner",
        chart="bar",
        dated="date",
        icon="storefront",
    ),
    Widget(
        "costs_by_type",
        "Costs by cost type",
        "chart",
        dashboard="finance",
        list="finance.booking",
        param=CENTER,
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
        param=CENTER,
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
        param=CENTER,
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
        param=CENTER,
        filter={"and": [THIS_YEAR, VALUE]},
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
        param=CENTER,
        filter={"and": [COSTS, {"field": "paid", "op": "empty"}]},
        icon="pending_actions",
    ),
)
