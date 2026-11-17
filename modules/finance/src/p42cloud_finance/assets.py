"""Assets: what an order adding value (CAPEX) became, and its yearly write-off.

An asset's value is what its order's CAPEX bookings cost, in the main currency; it's written off
evenly over the order's useful life, a part year by the months in use. Writing off a year books
the depreciation as an ordinary cost on the asset's center, so results show it.
"""

from datetime import date
from decimal import Decimal as Exact
from typing import Any

from p42cloud.sdk import (
    ActionInput,
    ActionResult,
    ActionRun,
    Change,
    Client,
    Currency,
    Date,
    FieldError,
    Integer,
    Money,
    Problem,
    Reference,
    String,
    Table,
    Text,
    UIAction,
)

ASSET = Table(
    "asset",
    "Asset",
    {
        "name": String("Name", max_length=160, required=True),
        "order": Reference("Order (adds value)", "costing.order", required=True),
        "in_use": Date("In use from", required=True),
        "cost_type": Reference("Booked as", "costing.cost_type", required=True),
        "note": Text("Note"),
        # Worked out from the order and its bookings, and by the write-offs.
        "center": Reference("Cost center", "costing.center", read_only=True),
        "useful_life": Integer("Written off over (years)", read_only=True),
        "currency": Currency("Currency", read_only=True),
        "value": Money("Value", currency_field="currency", read_only=True),
        "yearly": Money("Each year", currency_field="currency", read_only=True),
        "written_off": Money("Written off", currency_field="currency", read_only=True),
        "book_value": Money("Book value", currency_field="currency", read_only=True),
    },
    label_plural="Assets",
    display="name",
    comments=True,
)

WRITE_OFF = Table(
    "write_off",
    "Write-off",
    {
        "asset": Reference("Asset", "finance.asset", required=True),
        "year": Integer("Year", required=True),
        "currency": Currency("Currency", required=True),
        "amount": Money("Amount", currency_field="currency", required=True),
        "booking": Reference("Booking", "finance.booking", read_only=True),
    },
    label_plural="Write-offs",
    unique_together=(("asset", "year"),),
    display="year",
)

TABLES = (ASSET, WRITE_OFF)
CENT = Exact("0.01")


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
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


async def worth(client: Client, order: str) -> Exact:
    """What the order's CAPEX bookings cost, in the main currency."""
    bookings = await _all(
        client,
        "finance.booking",
        {
            "and": [
                {"field": "order", "op": "eq", "value": order},
                {"field": "capex", "op": "eq", "value": True},
            ]
        },
    )
    missing = [b["text"] for b in bookings if b.get("amount_main") is None]
    if missing:
        raise _invalid(
            "order", f"Some of its bookings have no amount in the main currency: {missing[0]}."
        )
    return sum((Exact(str(b["amount_main"])) for b in bookings), Exact(0))


async def check_asset(change: Change) -> None:
    """An asset comes from an order adding value; its center, life, value and yearly write-off
    follow from the order and its bookings, worked out again whenever the asset is saved."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    current = (
        await client.get("finance.asset", change.sys_id)
        if change.operation == "write" and change.sys_id is not None
        else {}
    )
    merged = current | change.values
    order = await client.get("costing.order", str(merged["order"]))
    if order["kind"] != "capex":
        raise _invalid("order", "An asset comes from an order adding value (CAPEX).")
    value = await worth(client, str(order["sys_id"]))
    years = int(order["useful_life"])
    written = Exact(str(merged.get("written_off") or "0"))
    change.values |= {
        "center": order["center"],
        "useful_life": years,
        "currency": str(await client.setting("sys.main_currency") or "CHF"),
        "value": str(value),
        "yearly": str((value / years).quantize(CENT)),
        "written_off": str(written),
        "book_value": str(value - written),
    }


def months_in_use(in_use: date, year: int) -> int:
    """The months of a year the asset was in use (the month it came into use counts)."""
    if year < in_use.year:
        return 0
    return 12 - in_use.month + 1 if year == in_use.year else 12


async def write_off(run: ActionRun) -> ActionResult:
    """Books a year's write-off: the yearly amount (a part year by months in use), never more than
    the book value left, once per year."""
    client = Client(run.records)
    asset = await client.get("finance.asset", run.sys_id)
    year = int(run.inputs["year"])
    done = await client.list(
        "finance.write_off",
        where={
            "and": [
                {"field": "asset", "op": "eq", "value": str(run.sys_id)},
                {"field": "year", "op": "eq", "value": year},
            ]
        },
    )
    if done["records"]:
        raise Problem(409, "written_off", f"{year} is written off already.")
    months = months_in_use(date.fromisoformat(asset["in_use"]), year)
    if months == 0:
        raise Problem(409, "not_in_use", f"The asset wasn't in use in {year}.")
    left = Exact(str(asset["book_value"]))
    amount = min((Exact(str(asset["yearly"])) * months / 12).quantize(CENT), left)
    if amount <= 0:
        raise Problem(409, "written_off", "Nothing is left to write off.")
    booking = await client.create(
        "finance.booking",
        {
            "date": f"{year}-12-31",
            "text": f"Depreciation {asset['name']} {year}",
            "cost_type": asset["cost_type"],
            "center": asset["center"],
            "currency": asset["currency"],
            "amount": str(amount),
        },
    )
    await client.create(
        "finance.write_off",
        {
            "asset": str(run.sys_id),
            "year": year,
            "currency": asset["currency"],
            "amount": str(amount),
            "booking": booking["sys_id"],
        },
    )
    written = Exact(str(asset["written_off"])) + amount
    fresh = await client.get("finance.asset", run.sys_id)
    await client.update("finance.asset", run.sys_id, {"written_off": str(written)}, fresh["_etag"])
    return ActionResult(message=f"{asset['currency']} {amount:,.2f} written off for {year}.")


ACTIONS = (
    UIAction(
        "write_off",
        "finance.asset",
        "Write off a year",
        places=("form_button",),
        icon="trending_down",
        inputs=(ActionInput("year", "Year", type="integer"),),
        handler=write_off,
    ),
)
