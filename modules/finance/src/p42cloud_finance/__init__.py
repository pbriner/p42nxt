"""Finance: what was spent and earned, booked against the cost structure, and the running costs
charged on to tenants.

A booking answers the four questions of Costing (whose result, where, what kind, for which
work) with the original invoice attached. What follows from its answers is worked out, never
typed: the profit center from the cost center, cost or income from the cost type, adding value
(CAPEX) from the order, and the amount in the main currency with the rate of the booking's day.
A rental links a lease (Partners) to the flat it's for (a cost center); a running-cost statement
collects the chargeable costs of a period for it, spread by Costing's keys, less the advances
the tenant paid.
"""

from datetime import date
from decimal import Decimal as Exact
from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    ActionResult,
    ActionRun,
    Boolean,
    Change,
    Choice,
    Client,
    Currency,
    Date,
    FieldError,
    Flow,
    FormSection,
    FormView,
    Grant,
    Item,
    ListView,
    Module,
    Money,
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
    UIAction,
    package_version,
)

from p42cloud_finance import assets, dashboard

ALL = ("read", "create", "write", "delete", "restore")

BOOKING = Table(
    "booking",
    "Booking",
    {
        "date": Date("Date", required=True),
        "text": String("Text", max_length=200, required=True),
        "partner": Reference("Partner", "partners.partner", on_delete="restrict"),
        "cost_type": Reference(
            "Cost type", "costing.cost_type", required=True, on_delete="restrict"
        ),
        "center": Reference("Cost center", "costing.center", on_delete="restrict"),
        "order": Reference("Order", "costing.order", on_delete="restrict"),
        "currency": Currency("Currency", required=True, default="CHF"),
        "amount": Money("Amount", currency_field="currency", required=True),
        "invoice_number": String("Invoice number", max_length=60),
        "paid": Date("Paid on"),
        # Worked out from the answers above, never typed.
        "profit_center": Reference("Profit center", "costing.center", read_only=True),
        "kind": Choice(
            "Kind",
            {"cost": "Cost", "income": "Income"},
            read_only=True,
            tones={"income": "primary"},
        ),
        "capex": Boolean("Adds value (CAPEX)", read_only=True),
        "main_currency": Currency("Main currency", read_only=True),
        "amount_main": Money(
            "In the main currency", currency_field="main_currency", read_only=True
        ),
        "note": Text("Note"),
    },
    label_plural="Bookings",
    display="text",
    files=True,
    comments=True,
)

RENTAL = Table(
    "rental",
    "Rental",
    {
        "contract": Reference("Lease", "partners.contract", required=True, on_delete="restrict"),
        "center": Reference("Flat", "costing.center", required=True, on_delete="restrict"),
        "active": Boolean("Active", required=True, default=True),
    },
    label_plural="Rentals",
    display="contract",
)

STATEMENT = Table(
    "statement",
    "Running-cost statement",
    {
        "rental": Reference("Rental", "finance.rental", required=True, on_delete="restrict"),
        "start": Date("From", required=True),
        "end": Date("To", required=True),
        "state": Choice(
            "State",
            {"draft": "Draft", "final": "Final"},
            required=True,
            default="draft",
            tones={"final": "primary"},
        ),
        "currency": Currency("Currency", read_only=True),
        "costs": Money("Running costs", currency_field="currency", read_only=True),
        "advances": Money("Advances paid", currency_field="currency", read_only=True),
        "due": Money("To pay (or refund, if negative)", currency_field="currency", read_only=True),
        "note": Text("Note"),
    },
    label_plural="Running-cost statements",
    display="rental",
    files=True,
    comments=True,
)

STATEMENT_LINE = Table(
    "statement_line",
    "Statement line",
    {
        "statement": Reference(
            "Statement", "finance.statement", required=True, on_delete="cascade"
        ),
        "kind": Choice(
            "Kind", {"cost": "Running cost", "advance": "Advance"}, required=True, default="cost"
        ),
        "cost_type": Reference("Cost type", "costing.cost_type", on_delete="restrict"),
        "text": String("Text", max_length=200, required=True),
        "currency": Currency("Currency", required=True),
        "amount": Money("Amount", currency_field="currency", required=True),
    },
    label_plural="Statement lines",
    display="text",
)

TABLES = (BOOKING, RENTAL, STATEMENT, STATEMENT_LINE, *assets.TABLES)
OWN = tuple(f"finance.{t.name}" for t in TABLES)

BOOKKEEPER = Role(
    "finance.bookkeeper",
    "Bookkeeper",
    grants=tuple(Grant(t, ALL) for t in OWN),
    includes=("costing.reader", "partners.reader"),
    description="Books costs and income, keeps rentals, works out running-cost statements.",
)
READER = Role(
    "finance.reader",
    "Finance reader",
    grants=tuple(Grant(t, ("read",)) for t in OWN),
    includes=("costing.reader", "partners.reader"),
    description="Reads bookings, rentals and statements.",
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


async def _in_main_currency(
    client: Client, amount: Any, currency: str, day: Any
) -> tuple[str, str | None]:
    """The main currency and the amount in it, with the rate of the day; none without a rate."""
    main = str(await client.setting("sys.main_currency") or "CHF")
    try:
        on = date.fromisoformat(str(day))
        return main, await client.convert(amount, currency, main, on=on)
    except Problem:
        return main, None


async def _check_booking(change: Change) -> None:
    """A booking names a cost center or an order; what follows from its answers is worked out:
    the profit center, cost or income, CAPEX, the amount in the main currency. Nothing is booked
    on a settled order."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    merged = await _current(change, "finance.booking") | change.values
    order = await client.get("costing.order", str(merged["order"])) if merged.get("order") else None
    if order is not None and order["state"] == "settled" and "order" in change.values:
        raise _invalid("order", "The order is settled: its costs are final.")
    if order is not None and not merged.get("center"):
        change.values["center"] = merged["center"] = order["center"]
    if not merged.get("center"):
        raise _invalid("center", "Name a cost center or an order.")
    center = await client.get("costing.center", str(merged["center"]))
    cost_type = await client.get("costing.cost_type", str(merged["cost_type"]))
    if Exact(str(merged["amount"])) <= 0:
        raise _invalid("amount", "An amount above zero: cost or income follows from the cost type.")
    change.values["profit_center"] = center.get("profit_center")
    change.values["kind"] = cost_type["kind"]
    change.values["capex"] = bool(order and order["kind"] == "capex")
    currency = str(merged.get("currency") or "CHF")  # the field's default, before it's stored
    main, converted = await _in_main_currency(client, merged["amount"], currency, merged["date"])
    change.values["main_currency"] = main
    change.values["amount_main"] = converted


async def _check_rental(change: Change) -> None:
    """A rental is a lease for a flat: a cost center."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    merged = await _current(change, "finance.rental") | change.values
    contract = await client.get("partners.contract", str(merged["contract"]))
    if contract["kind"] != "lease":
        raise _invalid("contract", "A rental is a lease.")
    center = await client.get("costing.center", str(merged["center"]))
    if center["kind"] != "cost":
        raise _invalid("center", "The flat is a cost center.")


async def _check_statement(change: Change) -> None:
    """A period that ends after it starts; a final statement isn't worked out again."""
    if change.operation in ("delete", "restore"):
        return
    merged = await _current(change, "finance.statement") | change.values
    if str(merged["end"]) < str(merged["start"]):
        raise _invalid("end", "The period ends after it starts.")


def _periods(start: date, end: date, every: str) -> Exact:
    """How many of the contract's periods a statement's span covers (whole months counted)."""
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    return {
        "month": Exact(months),
        "quarter": Exact(months) / 3,
        "year": Exact(months) / 12,
    }.get(every, Exact(0))


async def _list_all(client: Client, table: str, where: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await client.list(table, where=where, limit=500, after=after)
        found += page["records"]
        after = page["next"]
        if not after:
            return found


async def _shares(client: Client, flat: str) -> dict[str, Exact]:
    """For each shared center spread over the flat: the flat's part of it."""
    mine = await _list_all(
        client, "costing.key_share", {"field": "center", "op": "eq", "value": flat}
    )
    parts: dict[str, Exact] = {}
    for share in mine:
        key = await client.get("costing.key", str(share["key"]))
        if not key["active"]:
            continue
        all_shares = await _list_all(
            client, "costing.key_share", {"field": "key", "op": "eq", "value": key["sys_id"]}
        )
        total = sum((Exact(str(s["value"])) for s in all_shares), Exact(0))
        if total > 0:
            parts[str(key["center"])] = (
                parts.get(str(key["center"]), Exact(0)) + Exact(str(share["value"])) / total
            )
    return parts


async def _work_out(run: ActionRun) -> ActionResult:
    """The statement's lines: the chargeable running costs of the period (the flat's own, and its
    part of the shared centers), each cost type in one line, then the advances paid."""
    client = Client(run.records)
    statement = run.record
    if statement["state"] != "draft":
        raise Problem(409, "final", "A final statement isn't worked out again.")
    rental = await client.get("finance.rental", str(statement["rental"]))
    contract = await client.get("partners.contract", str(rental["contract"]))
    start, end = date.fromisoformat(statement["start"]), date.fromisoformat(statement["end"])
    main = str(await client.setting("sys.main_currency") or "CHF")
    chargeable = {
        t["sys_id"]: t
        for t in await _list_all(
            client, "costing.cost_type", {"field": "chargeable", "op": "eq", "value": True}
        )
    }
    parts = {str(rental["center"]): Exact(1)} | await _shares(client, str(rental["center"]))
    by_type: dict[str, Exact] = {}
    if chargeable:
        bookings = await _list_all(
            client,
            "finance.booking",
            {
                "and": [
                    {
                        "field": "date",
                        "op": "between",
                        "value": [statement["start"], statement["end"]],
                    },
                    {"field": "center", "op": "in", "value": sorted(parts)},
                    {"field": "cost_type", "op": "in", "value": sorted(chargeable)},
                    {"field": "kind", "op": "eq", "value": "cost"},
                    {"field": "capex", "op": "eq", "value": False},
                ]
            },
        )
        for booking in bookings:
            if booking.get("amount_main") is None:
                raise Problem(
                    422,
                    "no_rate",
                    f"The booking {booking['text']!r} has no amount in {main}: enter the rate.",
                )
            part = parts[str(booking["center"])]
            key = str(booking["cost_type"])
            by_type[key] = by_type.get(key, Exact(0)) + Exact(str(booking["amount_main"])) * part
    old = await _list_all(
        client,
        "finance.statement_line",
        {"field": "statement", "op": "eq", "value": str(run.sys_id)},
    )
    for line in old:
        await client.delete("finance.statement_line", line["sys_id"], line["_etag"])
    costs = Exact(0)
    for type_id, amount in sorted(by_type.items(), key=lambda kv: chargeable[kv[0]]["name"]):
        rounded = amount.quantize(Exact("0.01"))
        costs += rounded
        await client.create(
            "finance.statement_line",
            {
                "statement": str(run.sys_id),
                "kind": "cost",
                "cost_type": type_id,
                "text": chargeable[type_id]["name"],
                "currency": main,
                "amount": str(rounded),
            },
        )
    periods = _periods(start, end, str(contract.get("period") or "month"))
    advance = Exact(str(contract.get("advance") or "0"))
    advances = (advance * periods).quantize(Exact("0.01"))
    if advances:
        await client.create(
            "finance.statement_line",
            {
                "statement": str(run.sys_id),
                "kind": "advance",
                "text": f"Advances paid: {periods.normalize()} x {advance}",
                "currency": main,
                "amount": str(advances),
            },
        )
    fresh = await client.get("finance.statement", run.sys_id)
    await client.update(
        "finance.statement",
        run.sys_id,
        {
            "currency": main,
            "costs": str(costs),
            "advances": str(advances),
            "due": str(costs - advances),
        },
        fresh["_etag"],
    )
    due = costs - advances
    said = "to pay" if due >= 0 else "to refund"
    return ActionResult(message=f"Worked out: {main} {abs(due):,.2f} {said}.")


module = Module(
    id="finance",
    name="Finance",
    publisher="p42",
    version=package_version("p42cloud-finance"),
    depends_on={"sys": ">=0.46,<1", "costing": ">=0.3,<1", "partners": ">=0.1,<1"},
    tables=TABLES,
    roles=(BOOKKEEPER, READER),
    hooks=(
        TableHook("finance.booking", before=_check_booking),
        TableHook("finance.rental", before=_check_rental),
        TableHook("finance.statement", before=_check_statement),
        TableHook("finance.asset", before=assets.check_asset),
    ),
    flows=(
        Flow(
            "finance.statement",
            "state",
            initial="draft",
            transitions=(
                Transition(
                    "finalize",
                    "Make final",
                    "draft",
                    "final",
                    icon="lock",
                    confirm="Make the statement final? It isn't worked out again afterwards.",
                ),
            ),
        ),
    ),
    actions=(
        UIAction(
            "work_out",
            "finance.statement",
            "Work out",
            places=("form_button",),
            icon="calculate",
            condition={"field": "state", "op": "eq", "value": "draft"},
            handler=_work_out,
        ),
        *assets.ACTIONS,
    ),
    routes=dashboard.ROUTES,
    widgets=dashboard.WIDGETS,
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Bookings with their invoices, rentals and running-cost statements.",
    category="Finance",
    icon="account_balance",
    views=(
        ListView(
            "finance.booking",
            ("date", "text", "partner", "cost_type", "center", "amount", "amount_main", "paid"),
            sort=("-date",),
            search=("text", "invoice_number"),
        ),
        ListView("finance.rental", ("contract", "center", "active"), sort=("contract",)),
        ListView(
            "finance.asset",
            ("name", "order", "center", "in_use", "value", "written_off", "book_value"),
            sort=("name",),
            search=("name",),
        ),
        ListView(
            "finance.write_off",
            ("asset", "year", "amount"),
            sort=("asset", "year"),
            searchable=False,
        ),
        ListView(
            "finance.statement",
            ("rental", "start", "end", "state", "costs", "advances", "due"),
            sort=("-end",),
            searchable=False,
        ),
        ListView(
            "finance.statement_line",
            ("statement", "kind", "text", "amount"),
            sort=("statement",),
            searchable=False,
        ),
    ),
    forms=(
        FormView(
            "finance.booking",
            (
                FormSection("Booking", ("date", "text", "partner", "invoice_number", "paid")),
                FormSection("Amount", ("currency", "amount", "main_currency", "amount_main")),
                FormSection("Booked on", ("cost_type", "center", "order")),
                FormSection("Worked out", ("profit_center", "kind", "capex")),
                FormSection("Note", ("note",), columns=1),
            ),
        ),
        FormView("finance.rental", (FormSection("Rental", ("contract", "center", "active")),)),
        FormView(
            "finance.asset",
            (
                FormSection("Asset", ("name", "order", "in_use", "cost_type")),
                FormSection(
                    "Worked out",
                    (
                        "center",
                        "useful_life",
                        "currency",
                        "value",
                        "yearly",
                        "written_off",
                        "book_value",
                    ),
                ),
                FormSection("Note", ("note",), columns=1),
            ),
            related=(RelatedList("finance.write_off", "asset"),),
        ),
        FormView(
            "finance.statement",
            (
                FormSection("Statement", ("rental", "start", "end", "state")),
                FormSection("Result", ("currency", "costs", "advances", "due")),
                FormSection("Note", ("note",), columns=1),
            ),
            related=(RelatedList("finance.statement_line", "statement"),),
        ),
    ),
    navigation=(
        Section(
            "Finance",
            (
                Item(
                    "dashboard",
                    "Dashboard",
                    kind="page",
                    path="/dashboard/finance",
                    icon="monitoring",
                ),
                Item("bookings", "Bookings", "finance.booking", icon="receipt_long"),
                Item("rentals", "Rentals", "finance.rental", icon="apartment"),
                Item("assets", "Assets", "finance.asset", icon="domain"),
                Item(
                    "statements",
                    "Running-cost statements",
                    "finance.statement",
                    icon="request_quote",
                ),
            ),
        ),
    ),
)
