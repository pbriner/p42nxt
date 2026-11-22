"""The cycle: every few minutes each active plan is checked with its owner's Kraken connection,
and buys when it's due: a dry run, or a real order when the plan, its owner's key check and the
installation's switch all say so (on the production installation only).

Right before a real buy, two more checks: no other system trades the pair (else the plan holds
until its owner has looked), and the market looks normal (else it waits for the next check)."""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from p42cloud.sdk import Client, JobRun, Problem

from p42cloud_dca import kraken, stats, strategy
from p42cloud_dca.kraken import KrakenError, Pair
from p42cloud_dca.orders import apply_fill, hold, tell_bought
from p42cloud_dca.paging import every

FEE = Decimal("0.0026")  # Kraken's taker fee, for dry runs
MAX_MESSAGE = 500
UNCLEAR = timedelta(minutes=30)  # an order still open or unclear this long holds its plan
REAL = ("filled", "submitting", "unknown")  # what real orders spend
MAX_GAP = Decimal("0.02")  # buying and selling prices further apart: the market waits
MAX_MOVE = Decimal("0.10")  # further from yesterday's close: the market waits
WAITING = "Waiting:"


def _kraken(client: Client, plan: dict[str, Any]) -> Any:
    """The plan's Kraken account: its connection (its owner's only one, for plans before it was
    named)."""
    return client.connection(kraken.TYPE, owner=plan["owner"], connection=plan.get("connection"))


def _decimal(value: Any) -> Decimal | None:
    return None if value is None or value == "" else Decimal(str(value))


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    moment = datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _orders(client: Client, plan: dict[str, Any]) -> list[dict[str, Any]]:
    return await every(
        client,
        "dca.order",
        where={"field": "plan", "op": "eq", "value": plan["sys_id"]},
        sort=["placed_at"],
    )


def _counted(plan: dict[str, Any]) -> tuple[str, ...]:
    """The orders that count for a plan: its real ones when live, else its dry runs."""
    return REAL if plan["live"] else ("dry_run",)


def _spent(plan: dict[str, Any], orders: list[dict[str, Any]], since: datetime) -> Decimal:
    states = _counted(plan)
    return sum(
        (
            _decimal(o["cost"]) or Decimal(0)
            for o in orders
            if o["state"] in states and (_moment(o["placed_at"]) or since) >= since
        ),
        Decimal(0),
    )


def _reference(plan: dict[str, Any], orders: list[dict[str, Any]]) -> Decimal | None:
    states = ("filled",) if plan["live"] else ("dry_run",)
    done = [o for o in orders if o["state"] in states and _decimal(o["amount"])]
    if not done:
        return None
    if plan["reference"] == "avg_buy":
        cost = sum((_decimal(o["cost"]) or Decimal(0) for o in done), Decimal(0))
        amount = sum((_decimal(o["amount"]) or Decimal(0) for o in done), Decimal(0))
        return cost / amount if amount else None
    return _decimal(done[-1]["price"])


def _cooled_down(plan: dict[str, Any], orders: list[dict[str, Any]], now: datetime) -> bool:
    hours = _decimal(plan["cooldown_hours"]) or strategy.DEFAULT_COOLDOWN
    since = now - timedelta(hours=float(hours))
    return not any(
        (_moment(o["placed_at"]) or now) > since for o in orders if o["state"] != "failed"
    )


async def _tiers(client: Client, plan: dict[str, Any]) -> list[strategy.Tier]:
    found = await client.list(
        "dca.tier", where={"field": "plan", "op": "eq", "value": plan["sys_id"]}, limit=100
    )
    return [
        strategy.Tier(Decimal(str(t["threshold_percent"])), Decimal(str(t["amount"])), t["enabled"])
        for t in found["records"]
    ]


async def _buy(  # noqa: PLR0913 - what's bought, for whom, at what price, when
    client: Client,
    plan: dict[str, Any],
    pair: Pair,
    decision: strategy.Decision,
    *,
    price: Decimal,
    now: datetime,
) -> str:
    """A dry run, or a real order recorded with the call that sends it (same transaction)."""
    cost = _cents(decision.amount * price)
    cur = pair.currency
    said = f"{decision.amount} {pair.asset} at {price:.2f} {cur} ({cost} {cur})"
    if decision.message:  # bought less than asked: why
        said += f" ({decision.message.rstrip('.')}, so less than planned)"
    order = await client.create(
        "dca.order",
        {
            "owner": plan["owner"],
            "plan": plan["sys_id"],
            "pair": pair.name,
            "strategy": decision.strategy,
            "state": "submitting" if plan["live"] else "dry_run",
            "amount": str(decision.amount),
            "price": str(price),
            "cost": str(cost),
            "fee": str((cost * FEE).quantize(Decimal("0.0001"))),
            "currency": cur,
            "placed_at": now.isoformat(),
        },
    )
    if plan["live"]:
        await client.call(
            "dca.place_order",
            # The order's own ID is its reference at Kraken (client order ID).
            {"pair": pair.name, "volume": str(decision.amount), "cl_ord_id": order["sys_id"]},
            owner=plan["owner"],
            about=("dca.order", order["sys_id"]),
        )
        return f"Sending to Kraken: {said}."
    await client.notify(
        "dca.buy",
        f"Dry run: {plan['name']}",
        people=[plan["owner"]],
        message=f"Would have bought {said}.",
        link=f"/form/dca.plan/{plan['sys_id']}",
    )
    return f"Dry run: would have bought {said}."


async def _settle(
    client: Client,
    http: httpx.AsyncClient,
    plan: dict[str, Any],
    orders: list[dict[str, Any]],
    now: datetime,
) -> str | None:
    """Real orders not yet clear: asked about at Kraken. What the plan waits for, if anything;
    a plan never buys while one of its orders is unclear."""
    waiting: str | None = None
    for order in orders:
        if order["state"] not in ("submitting", "unknown"):
            continue
        ref = order["userref"] or order["sys_id"]
        if order["state"] == "submitting" and order["kraken_txid"]:
            fill = await kraken.order(http, order["kraken_txid"])
            if fill is not None and await apply_fill(client, order, fill.answer()) == "filled":
                await tell_bought(client, await client.get("dca.order", order["sys_id"]), plan)
                continue
            if fill is not None and fill.status in ("canceled", "expired"):
                continue
        waiting = f"Waiting for Kraken to confirm order {ref}."
        placed = _moment(order["placed_at"]) or now
        if order["state"] == "unknown" or now - placed > UNCLEAR:
            if plan["status"] != "hold":
                await hold(
                    client,
                    plan["sys_id"],
                    f"Order {ref} is unclear after 30 minutes: check it on Kraken, then Resume.",
                )
            return f"On hold: order {ref} is unclear."
    return waiting


async def _real_orders_stopped(client: Client) -> str | None:
    if not await client.setting("dca.live_allowed"):
        return (
            "Real orders are switched off for this installation"
            " (Settings → Real DCA orders allowed)."
        )
    stage = await client.setting("sys.stage")
    if stage != "production":
        return f"Not the production installation ({stage}): real orders are blocked here."
    return None


async def check_plan(client: Client, plan: dict[str, Any], now: datetime) -> tuple[str, bool]:
    """One plan's check: what it says, and whether it bought."""
    pair = Pair(plan["pair"].upper())
    orders = await _orders(client, plan)
    async with _kraken(client, plan) as http:
        if plan["live"] or any(o["state"] in ("submitting", "unknown") for o in orders):
            waiting = await _settle(client, http, plan, orders, now)
            if waiting is not None:
                return await _say(client, plan, waiting, now, await _price(http, pair)), False
        if plan["status"] == "hold":
            said = f"On hold: {plan['hold_reason']}"
            return await _say(client, plan, said, now, await _price(http, pair)), False
        if plan["live"] and (stopped := await _real_orders_stopped(client)):
            return await _say(client, plan, stopped, now, await _price(http, pair)), False
        price = await kraken.price(http, pair.name)
        market = await kraken.market(http, pair.name)
        free = await kraken.free_balance(http, pair.currency)
    since = strategy.period_start(now, plan["deposit_day"], plan["buy_hour"])
    spent = _spent(plan, orders, since)
    amount = Decimal(str(plan["amount"]))
    limit = _decimal(plan["monthly_limit"])
    plan_timing = strategy.timing(
        mode=plan["mode"],
        now=now,
        deposit_day=plan["deposit_day"],
        buy_hour=plan["buy_hour"],
        end_date=date.fromisoformat(plan["end_date"]) if plan["end_date"] else None,
        balance=free if plan["live"] else free - spent,  # dry runs spend it on paper
        price=price,
        amount=amount,
        monthly_limit=limit,
        spent=spent,
    )
    choice = strategy.choose_tier(
        await _tiers(client, plan) if plan["dynamic"] else [],
        amount,
        price,
        _reference(plan, orders),
    )
    decision = strategy.decide(
        now=now,
        planned=_moment(plan["next_buy"]),
        plan_timing=plan_timing,
        base=amount,
        choice=choice,
        dynamic=plan["dynamic"],
        cooled_down=_cooled_down(plan, orders, now),
        price=price,
        market=market,
        max_price=_decimal(plan["max_price"]),
        monthly_limit=limit,
        spent=spent,
        balance=free if plan["live"] else free - spent,
    )
    message = decision.message
    bought = False
    if decision.action == "buy" and plan["live"]:
        stopped = await _before_real_buy(client, plan, pair, orders, now)
        if stopped is not None:
            return await _say(client, plan, stopped, now, price), False
    if decision.action == "buy":
        message = await _buy(client, plan, pair, decision, price=price, now=now)
        bought = True
    await client.update(
        "dca.plan",
        plan["sys_id"],
        {
            "next_buy": decision.next_buy.isoformat() if decision.next_buy else None,
            "planned_buys": plan_timing.buys_left,
            "period_end": plan_timing.period_end.isoformat() if plan_timing.period_end else None,
            "last_price": str(price),
            "last_balance": str(free),
            "last_check": now.isoformat(),
            "last_message": message[:MAX_MESSAGE],
        },
        plan["_etag"],
    )
    return message, bought


async def _before_real_buy(
    client: Client, plan: dict[str, Any], pair: Pair, orders: list[dict[str, Any]], now: datetime
) -> str | None:
    """Why a real buy mustn't happen now, if anything: another system trades the pair (the
    plan holds), or the market looks wrong (it waits; the buy stays due)."""
    hours = float(_decimal(plan["cooldown_hours"]) or strategy.DEFAULT_COOLDOWN)
    since = now - timedelta(hours=hours)
    if (looked := _moment(plan["others_from"])) is not None:
        since = max(since, looked)
    # The owner's orders of every plan are ours: two plans on one pair don't hold each other.
    mine = await every(
        client,
        "dca.order",
        where={"field": "owner", "op": "eq", "value": plan["owner"]},
        sort=["placed_at"],
    )
    ours = {str(o[k]) for o in [*orders, *mine] for k in ("kraken_txid", "sys_id") if o[k]}
    async with _kraken(client, plan) as http:
        others = await kraken.others(http, pair.name, since, ours)
        ticker = await kraken.ticker(http, pair.name)
        close = await kraken.last_close(http, pair.name)
    if others:
        first = others[0]
        kind = "an open order" if first.status in ("open", "pending") else "a buy"
        reason = (
            f"Kraken has {kind} on {pair.name} this plan didn't place ({first.txid},"
            f" {first.at:%d.%m.%Y %H:%M} UTC): another system or someone by hand trades the"
            " same pair. Check it on Kraken, then Resume."
        )
        await hold(client, plan["sys_id"], reason)
        return f"On hold: {reason}"
    wrong = _market_wrong(ticker, close)
    if wrong is None:
        return None
    said = f"{WAITING} {wrong} The buy waits for the next check."
    if not str(plan["last_message"] or "").startswith(WAITING):
        await client.notify(
            "dca.problem",
            f"DCA plan waits: {plan['name']}",
            people=[plan["owner"]],
            message=said,
            link=f"/form/dca.plan/{plan['sys_id']}",
            severity="warning",
        )
    return await _say(client, plan, said, now)


def _market_wrong(ticker: kraken.Ticker, close: Decimal | None) -> str | None:
    if ticker.gap > MAX_GAP:
        return (
            f"Buying ({ticker.ask:,.2f}) and selling ({ticker.bid:,.2f}) are"
            f" {ticker.gap * 100:.1f} % apart, more than {MAX_GAP * 100:.0f} %."
        )
    if close and abs(ticker.last - close) / close > MAX_MOVE:
        return (
            f"The price ({ticker.last:,.2f}) is {(ticker.last - close) / close * 100:+.1f} %"
            f" from yesterday's close ({close:,.2f}), more than {MAX_MOVE * 100:.0f} %."
        )
    return None


async def _price(http: httpx.AsyncClient, pair: Pair) -> Decimal | None:
    """The price for a check that stops early; one Kraken can't give doesn't stop it."""
    try:
        return await kraken.price(http, pair.name)
    except KrakenError, httpx.HTTPError:
        return None


async def _say(
    client: Client,
    plan: dict[str, Any],
    message: str,
    now: datetime,
    price: Decimal | None = None,
) -> str:
    """What a check found. Its price goes with it: a check time without its price would pass an
    old price off as the current one."""
    values = {"last_message": message[:MAX_MESSAGE], "last_check": now.isoformat()}
    if price is not None:
        values["last_price"] = str(price)
    current = await client.get("dca.plan", plan["sys_id"])
    await client.update("dca.plan", plan["sys_id"], values, current["_etag"])
    return message


def _short(error: Exception) -> str:
    if isinstance(error, (KrakenError, Problem)):
        return str(error.title if isinstance(error, Problem) else error)
    return f"{type(error).__name__}: {error}"[:400]


async def run(job: JobRun) -> str:
    client = Client(job.records)
    active = await every(
        client, "dca.plan", where={"field": "active", "op": "eq", "value": True}, sort=["created"]
    )
    checked = bought = 0
    for plan in active:
        checked += 1
        try:
            _, did = await check_plan(client, plan, job.now)
            bought += did
        except (KrakenError, Problem, httpx.HTTPError, OSError, ValueError, KeyError) as error:
            await _problem(client, plan, _short(error), job.now)
    reached = await check_targets(client, job.now)
    said = f"{checked} plan(s) checked, {bought} bought."
    return said + (f" {reached} target(s) reached." if reached else "")


NAMES = {
    "profit_percent": ("Profit target", "target_profit_percent", "+{} %"),
    "profit": ("Profit target", "target_profit", "+{} {cur}"),
    "loss_percent": ("Loss limit", "loss_limit_percent", "-{} %"),
    "loss": ("Loss limit", "loss_limit", "-{} {cur}"),
}


async def _value_price(client: Client, plan: dict[str, Any], pair: Pair, now: datetime) -> Decimal:
    checked = _moment(plan["last_check"])
    last = _decimal(plan["last_price"])
    if last is not None and checked is not None and now - checked < timedelta(minutes=10):
        return last
    async with _kraken(client, plan) as http:
        return await kraken.price(http, pair.name)


async def check_targets(client: Client, now: datetime) -> int:
    """Each plan with targets (inactive ones too): its owner hears once each time one is
    reached; one no longer reached can be reached, and told, again."""
    plans = await every(client, "dca.plan")
    told = 0
    for plan in plans:
        targets = stats.Targets(*(_decimal(plan[NAMES[k][1]]) for k in NAMES))
        if all(t is None for t in vars(targets).values()):
            continue
        orders = await _orders(client, plan)
        real = [o for o in orders if o["state"] == "filled"]
        counted = real or [o for o in orders if o["state"] == "dry_run"]
        at = stats.position(
            [
                stats.Buy(
                    now,
                    _decimal(o["amount"]) or Decimal(0),
                    Decimal(0),
                    _decimal(o["cost"]) or Decimal(0),
                    _decimal(o["fee"]) or Decimal(0),
                    "",
                )
                for o in counted
            ]
        )
        pair = Pair(plan["pair"].upper())
        try:
            price = await _value_price(client, plan, pair, now)
        except KrakenError, Problem, httpx.HTTPError:
            continue  # asked again at the next check
        reached = stats.reached(targets, at, price)
        before = set(plan["targets_hit"] or [])
        for key in sorted(reached - before):
            told += 1
            await _tell_target(client, plan, key, at, price=price, pair=pair, dry=not real)
        if reached != before:
            current = await client.get("dca.plan", plan["sys_id"])
            await client.update(
                "dca.plan",
                plan["sys_id"],
                {"targets_hit": sorted(reached) or None},
                current["_etag"],
            )
    return told


async def _tell_target(  # noqa: PLR0913 - what was reached, for which plan, at what price
    client: Client,
    plan: dict[str, Any],
    key: str,
    at: stats.Position,
    *,
    price: Decimal,
    pair: Pair,
    dry: bool,
) -> None:
    name, field, form = NAMES[key]
    cur = pair.currency
    limit = form.format(f"{Decimal(str(plan[field])):,.2f}", cur=cur)
    profit = at.profit(price)
    percent = at.profit_percent(price) or Decimal(0)
    await client.notify(
        "dca.target",
        f"{name} reached: {plan['name']} {percent:+.2f} %",
        people=[plan["owner"]],
        message=(
            f"{name} {limit} reached at {price:,.2f} {cur}{' (dry runs)' if dry else ''}: "
            f"invested {at.invested:,.2f} {cur}, worth {at.value(price):,.2f} {cur}, "
            f"{'profit' if profit >= 0 else 'loss'} {abs(profit):,.2f} {cur} after fees."
        ),
        link=f"/dashboard/dca?plan={plan['sys_id']}",
        severity="success" if key.startswith("profit") else "warning",
    )


async def _problem(client: Client, plan: dict[str, Any], said: str, now: datetime) -> None:
    """A plan that can't be checked says so; its owner hears of it the first time."""
    first = not str(plan["last_message"] or "").startswith("Problem:")
    current = await client.get("dca.plan", plan["sys_id"])
    await client.update(
        "dca.plan",
        plan["sys_id"],
        {"last_message": f"Problem: {said}"[:MAX_MESSAGE], "last_check": now.isoformat()},
        current["_etag"],
    )
    if first:
        await client.notify(
            "dca.problem",
            f"DCA plan has a problem: {plan['name']}",
            people=[plan["owner"]],
            message=said,
            link=f"/form/dca.plan/{plan['sys_id']}",
            severity="error",
        )
