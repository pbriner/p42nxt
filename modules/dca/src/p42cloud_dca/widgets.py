"""The dashboard's figures and charts for one plan, as source widgets: each a route of the
module, read as the person (so only their own plan), real orders or else dry runs."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from p42cloud.sdk import Client, Problem, Route, RouteRequest, Widget

from p42cloud_dca import kraken, stats
from p42cloud_dca.kraken import KrakenError, Pair
from p42cloud_dca.paging import every
from p42cloud_dca.pairs import plan_pair

FRESH = timedelta(minutes=10)  # a plan's last price this recent is used as it is


def _d(value: Any) -> Decimal | None:
    return None if value is None or value == "" else Decimal(str(value))


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def money(value: Decimal | None, currency: str = "") -> str:
    return "—" if value is None else f"{value:,.2f} {currency}".strip()


def time_of(value: datetime | None) -> str:
    return "—" if value is None else f"{value:%d.%m.%Y %H:%M} UTC"


@dataclass
class View:
    """One plan as a widget sees it."""

    plan: dict[str, Any]
    pair: Pair
    real: bool
    buys: list[stats.Buy]
    client: Client
    now: datetime

    @property
    def subtitle(self) -> str:
        return f"{self.plan['name']} · {'real orders' if self.real else 'dry runs'}"

    @property
    def link(self) -> str:
        return f"/form/dca.plan/{self.plan['sys_id']}"

    async def price(self) -> tuple[Decimal | None, str]:
        """The price to value it at, and where it's from."""
        checked = _when(self.plan["last_check"])
        last = _d(self.plan["last_price"])
        if last is not None and checked is not None and self.now - checked < FRESH:
            return last, "at the last check"
        try:
            async with self.client.connection(kraken.TYPE) as http:
                return await kraken.price(http, self.pair), "Kraken, now"
        except Problem, KrakenError, httpx.HTTPError:
            pass
        if last is not None:
            return last, f"last check {time_of(checked)}"
        return (self.buys[-1].price, "the last buy") if self.buys else (None, "no price yet")

    def answer(self, **found: Any) -> dict[str, Any]:
        return {"subtitle": self.subtitle, "link": self.link} | found


async def view(request: RouteRequest) -> View:
    plan_id = request.query.get("plan")
    if not plan_id:
        raise Problem(422, "validation", "Choose a plan.")
    client = Client.of(request)
    plan = await client.get("dca.plan", plan_id)  # someone else's plan isn't there for them
    orders = await every(
        client,
        "dca.order",
        where={"field": "plan", "op": "eq", "value": plan["sys_id"]},
        sort=["placed_at"],
    )
    real = any(o["state"] == "filled" for o in orders)
    wanted = "filled" if real else "dry_run"
    buys = [
        stats.Buy(
            when=_when(o["placed_at"]) or _when(o["created"]) or datetime.now(UTC),
            amount=_d(o["amount"]) or Decimal(0),
            price=_d(o["price"]) or Decimal(0),
            cost=_d(o["cost"]) or Decimal(0),
            fee=_d(o["fee"]) or Decimal(0),
            strategy=str(o["strategy"]),
        )
        for o in orders
        if o["state"] == wanted
    ]
    return View(plan, await plan_pair(client, plan), real, buys, client, datetime.now(UTC))


# ---- figures ----


async def _running_mode(v: View) -> dict[str, Any]:
    p = v.plan
    if not p["active"]:
        text, tone = "Inactive", None
    elif p["status"] == "hold":
        text, tone = "On hold", "bad"
    elif p["live"]:
        text, tone = "Real orders", "good"
    else:
        text, tone = "Dry runs", None
    when = (
        f"until {p['end_date']}"
        if p["mode"] == "lump_sum"
        else f"until deposit day {p['deposit_day']} at {p['buy_hour']:02d}:00 UTC"
    )
    tiers = " · price tiers on" if p["dynamic"] else ""
    return v.answer(text=text, tone=tone, detail=f"Spread {when}{tiers}.")


async def _balance(v: View) -> dict[str, Any]:
    free = _d(v.plan["last_balance"])
    if free is None:
        return v.answer(text="—", detail="After the next check (every 5 minutes).")
    short = v.plan["active"] and v.plan["status"] != "hold" and v.plan["planned_buys"] == 0
    return v.answer(
        value=float(free),
        unit=v.pair.currency,
        detail=("Not enough for a buy · " if short else "")
        + f"free on Kraken, {time_of(_when(v.plan['last_check']))}",
        tone="bad" if short else None,
    )


async def _invested(v: View) -> dict[str, Any]:
    at = stats.position(v.buys)
    return v.answer(value=float(at.invested), unit=v.pair.currency, detail=f"{at.buys} buy(s)")


async def _bought(v: View) -> dict[str, Any]:
    at = stats.position(v.buys)
    return v.answer(text=f"{at.amount:.8f}", unit=v.pair.asset, detail=f"{at.buys} buy(s)")


async def _average_price(v: View) -> dict[str, Any]:
    average = stats.position(v.buys).average_price
    if average is None:
        return v.answer(text="—", detail="No buys yet.")
    return v.answer(value=float(round(average, 2)), unit=v.pair.currency)


async def _current_value(v: View) -> dict[str, Any]:
    price, source = await v.price()
    if price is None:
        return v.answer(text="—", detail="No price yet.")
    at = stats.position(v.buys)
    return v.answer(
        value=float(round(at.value(price), 2)),
        unit=v.pair.currency,
        detail=f"at {money(price, v.pair.currency)} ({source})",
    )


async def _profit(v: View) -> dict[str, Any]:
    price, _ = await v.price()
    at = stats.position(v.buys)
    if price is None or not at.buys:
        return v.answer(text="—", detail="No buys yet.")
    profit = at.profit(price)
    percent = at.profit_percent(price)
    return v.answer(
        value=float(round(profit, 2)),
        unit=v.pair.currency,
        detail=f"{percent:+.2f} % after fees" if percent is not None else "after fees",
        tone="good" if profit >= 0 else "bad",
    )


async def _fees(v: View) -> dict[str, Any]:
    at = stats.position(v.buys)
    return v.answer(
        value=float(round(at.fees, 2)),
        unit=v.pair.currency,
        detail="Kraken's fees" if v.real else "estimated",
    )


async def _last_buy(v: View) -> dict[str, Any]:
    if not v.buys:
        return v.answer(text="—", detail="No real orders yet." if v.real else "No dry runs yet.")
    last = v.buys[-1]
    return v.answer(
        text=time_of(last.when),
        detail=f"{last.amount} {v.pair.asset} at {money(last.price, v.pair.currency)}",
    )


async def _next_buy(v: View) -> dict[str, Any]:
    p = v.plan
    if not p["active"]:
        return v.answer(text="Inactive", detail="Switch the plan to active to buy.")
    if p["status"] == "hold":
        return v.answer(text="On hold", detail=p["hold_reason"], tone="bad")
    when = _when(p["next_buy"])
    if when is not None:
        return v.answer(text=time_of(when), detail="real order" if p["live"] else "dry run")
    return v.answer(text="Not planned", detail=p["last_message"] or "After the next check.")


async def _planned_buys(v: View) -> dict[str, Any]:
    p = v.plan
    if not p["active"]:
        return v.answer(text="—", detail="The plan is inactive.")
    if p["planned_buys"] is None:
        return v.answer(text="—", detail="After the next check (every 5 minutes).")
    return v.answer(value=p["planned_buys"], detail=f"until {time_of(_when(p['period_end']))}")


async def _targets(v: View) -> dict[str, Any]:
    p, cur = v.plan, v.pair.currency
    shown: list[str] = []
    for field, form in (
        ("target_profit_percent", "+{} %"),
        ("target_profit", "+{} " + cur),
        ("loss_limit_percent", "-{} %"),
        ("loss_limit", "-{} " + cur),
    ):
        number = _d(p[field])
        if number is not None:
            shown.append(form.format(f"{number:,.2f}"))
    if not shown:
        return v.answer(text="—", detail="None set (in the plan).")
    hit = set(p["targets_hit"] or [])
    if hit & {"profit", "profit_percent"}:
        detail, tone = "Profit target reached", "good"
    elif hit & {"loss", "loss_percent"}:
        detail, tone = "Loss limit reached", "bad"
    else:
        detail, tone = "None reached", None
    return v.answer(text=" · ".join(shown), detail=detail, tone=tone)


# ---- charts ----


def _points(
    values: list[tuple[str, Decimal | float | int]], key: bool = False
) -> list[dict[str, Any]]:
    return [
        {"label": label, "value": float(value)} | ({"key": label} if key else {})
        for label, value in values
    ]


def _day(moment: datetime) -> str:
    return f"{moment:%d.%m.%Y}"


async def _profit_over_time(v: View) -> dict[str, Any]:
    price, _ = await v.price()
    closes: list[tuple[datetime, Decimal]] = []
    if v.buys:
        try:
            async with v.client.connection(kraken.TYPE) as http:
                closes = await kraken.closes(http, v.pair)
        except Problem, KrakenError, httpx.HTTPError:
            closes = []
    found = stats.profit_by_day(v.buys, closes, price, v.now)
    return v.answer(
        subtitle=f"{v.subtitle} · profit or loss after fees, {v.pair.currency}",
        points=_points([(_day(d), round(p, 2)) for d, p in found]),
    )


async def _invested_per_month(v: View) -> dict[str, Any]:
    months = stats.per_month(v.buys, now=v.now)
    return v.answer(
        subtitle=f"{v.subtitle} · {v.pair.currency}",
        points=_points([(m, round(x, 2)) for m, x in months]),
    )


async def _buy_prices(v: View) -> dict[str, Any]:
    return v.answer(points=_points([(_day(b.when), round(b.price, 2)) for b in v.buys]))


async def _average_over_time(v: View) -> dict[str, Any]:
    return v.answer(
        points=_points([(_day(s.when), round(s.average, 2)) for s in stats.steps(v.buys)])
    )


async def _holdings(v: View) -> dict[str, Any]:
    return v.answer(
        subtitle=f"{v.subtitle} · {v.pair.asset}",
        points=_points([(_day(s.when), s.holdings) for s in stats.steps(v.buys)]),
    )


async def _invested_over_time(v: View) -> dict[str, Any]:
    return v.answer(
        points=_points([(_day(s.when), round(s.invested, 2)) for s in stats.steps(v.buys)])
    )


async def _buys_by_type(v: View) -> dict[str, Any]:
    names = {"scheduled": "Planned", "dynamic": "Extra (tiers)", "manual": "By hand"}
    counts = stats.by_strategy(v.buys)
    return v.answer(points=_points([(names.get(k, k), n) for k, n in counts.items()], key=True))


@dataclass(frozen=True)
class Shown:
    name: str
    title: str
    kind: str  # figure, or a chart: line, column, donut
    work: Callable[[View], Awaitable[dict[str, Any]]]
    icon: str | None = None
    accent: int = 1
    wide: bool = False


SHOWN = (
    Shown("running_mode", "Running mode", "figure", _running_mode, "play_circle", 6),
    Shown("balance", "Kraken balance", "figure", _balance, "account_balance", 5),
    Shown("invested", "Invested", "figure", _invested, "payments", 1),
    Shown("bought", "Bought", "figure", _bought, "currency_bitcoin", 4),
    Shown("average_price", "Average price", "figure", _average_price, "price_check", 7),
    Shown("current_value", "Current value", "figure", _current_value, "account_balance_wallet", 3),
    Shown("profit", "Profit / loss", "figure", _profit, "trending_up", 2),
    Shown("fees", "Fees", "figure", _fees, "receipt", 5),
    Shown("last_buy", "Last buy", "figure", _last_buy, "history", 1),
    Shown("next_buy", "Next buy", "figure", _next_buy, "schedule", 7),
    Shown("planned_buys", "Planned buys", "figure", _planned_buys, "event_repeat", 8),
    Shown("targets", "Targets", "figure", _targets, "flag", 6),
    Shown(
        "profit_over_time",
        "Profit / loss over time",
        "line",
        _profit_over_time,
        accent=2,
        wide=True,
    ),
    Shown("invested_per_month", "Invested per month", "column", _invested_per_month, accent=1),
    Shown("buy_prices", "Buy prices", "line", _buy_prices, accent=7),
    Shown("average_price_over_time", "Average buy price", "line", _average_over_time, accent=5),
    Shown("holdings", "Holdings", "line", _holdings, accent=4),
    Shown("invested_over_time", "Invested over time", "line", _invested_over_time, accent=3),
    Shown("buys_by_type", "Buys by type", "donut", _buys_by_type, accent=1),
)


def _route(shown: Shown) -> Route:
    async def handler(request: RouteRequest) -> dict[str, Any]:
        return await shown.work(await view(request))

    return Route("GET", f"/widget/{shown.name}", handler, f"The {shown.title.lower()} of a plan.")


ROUTES = tuple(_route(s) for s in SHOWN)

WIDGETS = tuple(
    Widget(
        s.name,
        s.title,
        "figure" if s.kind == "figure" else "chart",
        list="dca.plan",
        source=f"/widget/{s.name}",
        param=("plan", "dca.plan"),
        chart=None if s.kind == "figure" else s.kind,  # type: ignore[arg-type]
        dashboard="dca",
        icon=s.icon,
        accent=s.accent,
        sizes=("wide", "large") if s.wide else ("s", "wide", "large"),
        size="wide" if s.wide else "s",
    )
    for s in SHOWN
)
