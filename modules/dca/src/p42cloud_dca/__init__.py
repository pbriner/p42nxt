"""Kraken DCA: dollar cost averaging on each person's own Kraken account.

People connect their Kraken keys and make plans: what to buy, how much at a time, and until when
the money should last. A job checks every plan every few minutes and buys when it's due. In this
version every buy is a dry run: recorded and notified, never sent to Kraken.
"""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    Boolean,
    Change,
    Choice,
    Client,
    Date,
    DateTime,
    FieldError,
    FormSection,
    FormView,
    Grant,
    Integer,
    Invalid,
    Item,
    Job,
    ListView,
    Module,
    NotificationKind,
    Problem,
    Reference,
    RelatedList,
    Role,
    Schedule,
    Section,
    Setting,
    String,
    Table,
    TableHook,
    Text,
    UIAction,
)
from p42cloud.sdk import Decimal as Number

from p42cloud_dca import cycle
from p42cloud_dca.kraken import KRAKEN
from p42cloud_dca.orders import PLACE_ORDER


def _yes_or_no(value: Any) -> bool:
    if not isinstance(value, bool):
        raise Invalid("Yes or no.")
    return value


# Off until an administrator allows real orders for the whole installation.
LIVE_ALLOWED = Setting(
    "dca.live_allowed",
    "Real DCA orders allowed",
    False,
    check=_yes_or_no,
    help="Off: no DCA plan sends real orders to Kraken, whatever its own switch says.",
    public=True,
)

PAIRS = {
    "xbtchf": "BTC / CHF",
    "xbteur": "BTC / EUR",
    "xbtusd": "BTC / USD",
    "ethchf": "ETH / CHF",
    "etheur": "ETH / EUR",
    "ethusd": "ETH / USD",
}
# What the cycle writes on a plan; people change the rest.
BOOKKEEPING = (
    "status",
    "hold_reason",
    "next_buy",
    "planned_buys",
    "period_end",
    "last_price",
    "last_balance",
    "last_check",
    "last_message",
)

PLAN = Table(
    "plan",
    "DCA plan",
    {
        "name": String("Name", max_length=100, required=True),
        "owner": Reference("Owner", "sys.person", required=True),
        "pair": Choice("Pair", PAIRS, required=True, default="xbtchf"),
        "mode": Choice(
            "Spread until",
            {"recurring": "The next deposit day", "lump_sum": "An end date"},
            required=True,
            default="recurring",
        ),
        "amount": Number("Amount per buy", scale=8, required=True),
        "deposit_day": Integer("Deposit day (1 to 28)", required=True, default=24),
        "buy_hour": Integer("Hour (UTC, 0 to 23)", required=True, default=8),
        "end_date": Date("End date"),
        "max_price": Number("Never above this price"),
        "monthly_limit": Number("At most per month"),
        "dynamic": Boolean("Price tiers", required=True, default=False),
        "reference": Choice(
            "Tiers compare with",
            {"last_buy": "The last buy", "avg_buy": "The average buy price"},
            required=True,
            default="last_buy",
        ),
        "cooldown_hours": Number("Hours between extra buys", precision=8, scale=2),
        "active": Boolean("Active", required=True, default=True),
        # Off: dry runs. Only the owner switches it, after checking their key.
        "live": Boolean("Real orders", required=True, default=False),
        "keys_checked": Boolean("My Kraken key can't withdraw", required=True, default=False),
        "status": Choice(
            "Status",
            {"ok": "Running", "hold": "On hold"},
            required=True,
            default="ok",
            tones={"hold": "error"},
        ),
        "hold_reason": Text("Why on hold"),
        "next_buy": DateTime("Next buy"),
        "planned_buys": Integer("Buys left in the period"),
        "period_end": DateTime("Period ends"),
        "last_price": Number("Last price", scale=8),
        "last_balance": Number("Free on Kraken", scale=8),
        "last_check": DateTime("Last checked"),
        "last_message": Text("Last said"),
    },
    label_plural="DCA plans",
    owner="owner",
    display="name",
)

TIER = Table(
    "tier",
    "Price tier",
    {
        "owner": Reference("Owner", "sys.person", required=True),
        "plan": Reference("Plan", "dca.plan", required=True),
        # Against the reference price: -5 for 5 % lower, 10 for 10 % higher.
        "threshold_percent": Number("Price change (%)", precision=8, scale=2, required=True),
        "amount": Number("Amount (0: skip)", scale=8, required=True),
        "enabled": Boolean("On", required=True, default=True),
    },
    label_plural="Price tiers",
    owner="owner",
    display="threshold_percent",
)

ORDER = Table(
    "order",
    "DCA order",
    {
        "owner": Reference("Owner", "sys.person", required=True),
        "plan": Reference("Plan", "dca.plan", required=True),
        "pair": String("Pair", max_length=20, required=True),
        "strategy": Choice(
            "Why",
            {"scheduled": "Planned", "dynamic": "Extra (tiers)", "manual": "By hand"},
            required=True,
        ),
        "state": Choice(
            "State",
            {
                "dry_run": "Dry run",
                "submitting": "Sending",
                "filled": "Bought",
                "unknown": "Answer lost",
                "failed": "Failed",
            },
            required=True,
            tones={"filled": "primary", "failed": "error", "unknown": "tertiary"},
        ),
        "amount": Number("Amount", scale=8, required=True),
        "price": Number("Price", scale=8),
        "cost": Number("Cost", scale=8),
        "fee": Number("Fee", scale=8),
        "currency": String("Currency", max_length=3),
        "userref": Integer("Client reference"),
        "kraken_txid": String("Kraken order", max_length=64, unique=True),
        "placed_at": DateTime("When"),
        "reconciled_at": DateTime("Checked with Kraken"),
        "error": Text("What went wrong"),
    },
    label_plural="DCA orders",
    owner="owner",
    display="placed_at",
)

PEOPLE_WRITE = (
    "name",
    "pair",
    "mode",
    "amount",
    "deposit_day",
    "buy_hour",
    "end_date",
    "max_price",
    "monthly_limit",
    "dynamic",
    "reference",
    "cooldown_hours",
    "active",
    "live",
    "keys_checked",
    "status",  # only back to running, with Resume
)

USER = Role(
    "dca.user",
    "DCA investor",
    grants=(
        Grant("dca.plan", ("read", "create", "delete", "restore")),
        Grant("dca.plan", ("write",), fields=PEOPLE_WRITE),
        Grant("dca.tier", ("read", "create", "write", "delete", "restore")),
        Grant("dca.order", ("read",)),
    ),
    description="Makes their own DCA plans; sees only their own plans and orders.",
)

# For the cycle (its service account): writes orders and what it found on the plans.
KEEPER = Role(
    "dca.keeper",
    "DCA cycle",
    grants=(
        Grant("dca.plan", ("read",)),
        Grant("dca.plan", ("write",), fields=BOOKKEEPING),
        Grant("dca.tier", ("read",)),
        Grant("dca.order", ("read", "create", "write")),
    ),
    description="Checks the plans and records their orders: for the DCA cycle.",
)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


def _number(value: Any, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise _invalid(field, "A number.") from error


def _check_values(values: dict[str, Any], merged: dict[str, Any]) -> None:
    for field, low, high in (("deposit_day", 1, 28), ("buy_hour", 0, 23)):
        if field in values and not low <= int(values[field]) <= high:
            raise _invalid(field, f"From {low} to {high}.")
    amount = _number(merged.get("amount"), "amount")
    if amount is not None and amount <= 0:
        raise _invalid("amount", "More than 0.")
    if merged.get("live") and not merged.get("keys_checked"):
        raise _invalid(
            "keys_checked", "Check that your Kraken key can't withdraw money before real orders."
        )
    for field in ("max_price", "monthly_limit", "cooldown_hours"):
        found = _number(values.get(field), field)
        if found is not None and found <= 0:
            raise _invalid(field, "More than 0, or empty.")
    if merged.get("mode") == "lump_sum" and not merged.get("end_date"):
        raise _invalid("end_date", "A plan spread until an end date needs that date.")


# Changing these plans the next buy again.
TIMING = frozenset(
    {
        "amount",
        "mode",
        "deposit_day",
        "buy_hour",
        "end_date",
        "pair",
        "active",
        "monthly_limit",
        "live",
    }
)


def _by_cycle(change: Change) -> bool:
    """The cycle (the module's service account) records orders; people never do."""
    return bool(change.records.access.of("dca.order", "create"))


async def _check_status(change: Change, current: dict[str, Any]) -> None:
    """People only resume a plan on hold; holding is the cycle's."""
    values = change.values
    if _by_cycle(change):
        return
    if change.operation == "create":  # a new plan runs, whatever the form sent
        values["status"] = "ok"
        values.pop("hold_reason", None)
        return
    for field in ("status", "hold_reason"):  # the form sends unchanged fields too
        if field in values and values[field] == current.get(field):
            del values[field]
    if not ({"status", "hold_reason"} & set(values)):
        return
    if "hold_reason" in values or values.get("status") != "ok" or current.get("status") != "hold":
        raise _invalid("status", "Only a plan on hold can be resumed, with Resume.")
    values |= {
        "hold_reason": None,
        "next_buy": None,
        "last_message": "Resumed: the next check plans the next buy.",
    }


async def _check_live(change: Change) -> None:
    """Real orders need a Kraken connection that answered its check."""
    if not change.values.get("live") or _by_cycle(change):
        return
    found = await Client.of(change).list(
        "sys.connection", where={"field": "type", "op": "eq", "value": "dca.kraken"}, limit=1
    )
    if not found["records"] or found["records"][0]["health"] != "ok":
        raise _invalid("live", "Check your Kraken connection first (Connections → Check).")


async def _check_plan(change: Change) -> None:
    """A plan is its maker's; its settings make sense; saving never buys."""
    if change.operation in ("delete", "restore"):
        return
    values = change.values
    if change.operation == "create":
        values["owner"] = change.person
    current: dict[str, Any] = {}
    if change.operation == "write" and change.sys_id is not None:
        current = await Client.of(change).get("dca.plan", change.sys_id)
    merged = current | values
    _check_values(values, merged)
    await _check_status(change, current)
    await _check_live(change)
    if set(values) & TIMING:
        values["next_buy"] = None
        values["last_message"] = (
            "Inactive: no buys."
            if not merged.get("active", True)
            else "The next buy is planned at the next check (within 5 minutes)."
        )


async def _check_tier(change: Change) -> None:
    """A tier belongs to one of its maker's own plans."""
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["owner"] = change.person
        # Reading the plan as the person: someone else's plan isn't there for them.
        await Client.of(change).get("dca.plan", str(change.values.get("plan")))
    elif "plan" in change.values:
        raise _invalid("plan", "A tier stays with its plan.")


module = Module(
    id="dca",
    name="Kraken DCA",
    version="0.2.0",
    depends_on={"sys": ">=0.20,<1"},
    tables=(PLAN, TIER, ORDER),
    roles=(USER, KEEPER),
    hooks=(
        TableHook("dca.plan", before=_check_plan),
        TableHook("dca.tier", before=_check_tier),
    ),
    docs=Path(__file__).parent / "docs",
    description="Dollar cost averaging on your own Kraken account.",
    category="Finance",
    icon="currency_bitcoin",
    connection_types=(KRAKEN,),
    settings=(LIVE_ALLOWED,),
    calls=(PLACE_ORDER,),
    actions=(
        UIAction(
            "resume",
            "dca.plan",
            "Resume",
            places=("form_button", "list_row"),
            icon="play_arrow",
            condition={"field": "status", "op": "eq", "value": "hold"},
            confirm="Resume this plan? Check its orders on Kraken first.",
            set={"status": "ok"},
            message="Resumed.",
        ),
    ),
    notification_kinds=(
        NotificationKind("buy", "A buy (or dry run)", "To the plan's owner, at each buy."),
        NotificationKind(
            "hold",
            "A DCA plan is on hold",
            "To the plan's owner: Kraken refused an order, or one is unclear.",
            channels=("telegram",),
        ),
        NotificationKind(
            "problem",
            "A DCA plan has a problem",
            "To the plan's owner, the first time a check fails.",
            channels=("telegram",),
        ),
    ),
    jobs=(Job("cycle", "Check the DCA plans", cycle.run, timeout=240, retries=0),),
    schedules=(Schedule("cycle", "DCA cycle", "cycle", "*/5 * * * *"),),
    views=(
        ListView(
            "dca.plan",
            ("name", "pair", "amount", "active", "live", "status", "next_buy", "last_message"),
            sort=("name",),
            search=("name",),
        ),
        ListView(
            "dca.tier",
            ("plan", "threshold_percent", "amount", "enabled"),
            sort=("-threshold_percent",),
        ),
        ListView(
            "dca.order",
            ("placed_at", "plan", "strategy", "state", "amount", "price", "cost", "currency"),
            sort=("-placed_at",),
            searchable=False,
        ),
    ),
    forms=(
        FormView(
            "dca.plan",
            (
                FormSection("Plan", ("name", "pair", "amount", "active")),
                FormSection("Real orders", ("live", "keys_checked", "status", "hold_reason")),
                FormSection("When", ("mode", "deposit_day", "buy_hour", "end_date")),
                FormSection("Limits", ("max_price", "monthly_limit")),
                FormSection("Price tiers", ("dynamic", "reference", "cooldown_hours")),
                FormSection(
                    "What it found",
                    (
                        "next_buy",
                        "planned_buys",
                        "period_end",
                        "last_price",
                        "last_balance",
                        "last_check",
                        "last_message",
                    ),
                ),
            ),
            related=(RelatedList("dca.tier", "plan"), RelatedList("dca.order", "plan")),
        ),
    ),
    navigation=(
        Section(
            "Kraken DCA",
            (
                Item(
                    "plans",
                    "Plans",
                    "dca.plan",
                    icon="savings",
                    keywords=("bitcoin", "kraken", "dollar cost averaging"),
                ),
                Item(
                    "orders",
                    "Orders",
                    "dca.order",
                    icon="receipt_long",
                    keywords=("buys", "bitcoin"),
                ),
                Item("new_plan", "New plan", "dca.plan", kind="create", icon="add"),
            ),
        ),
    ),
)
