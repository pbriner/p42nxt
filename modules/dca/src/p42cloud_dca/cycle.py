"""The cycle: every few minutes each active plan is checked with its owner's Kraken connection,
and buys when it's due. In this version every buy is a dry run: recorded, never sent."""

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from p42cloud.sdk import Client, JobRun, Problem

from p42cloud_dca import kraken, strategy
from p42cloud_dca.kraken import KrakenError, Pair

FEE = Decimal("0.0026")  # Kraken's taker fee, for dry runs
MAX_MESSAGE = 500


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
    found = await client.list(
        "dca.order",
        where={"field": "plan", "op": "eq", "value": plan["sys_id"]},
        sort=["placed_at"],
        limit=500,
    )
    return found["records"]


def _spent(orders: list[dict[str, Any]], since: datetime) -> Decimal:
    return sum(
        (
            _decimal(o["cost"]) or Decimal(0)
            for o in orders
            if o["state"] == "dry_run" and (_moment(o["placed_at"]) or since) >= since
        ),
        Decimal(0),
    )


def _reference(plan: dict[str, Any], orders: list[dict[str, Any]]) -> Decimal | None:
    done = [o for o in orders if o["state"] == "dry_run" and _decimal(o["amount"])]
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


async def check_plan(client: Client, plan: dict[str, Any], now: datetime) -> tuple[str, bool]:
    """One plan's check: what it says, and whether it bought (a dry run)."""
    pair = Pair(plan["pair"].upper())
    orders = await _orders(client, plan)
    async with client.connection(kraken.TYPE, owner=plan["owner"]) as http:
        price = await kraken.price(http, pair.name)
        market = await kraken.market(http, pair.name)
        free = await kraken.free_balance(http, pair.currency)
    since = strategy.period_start(now, plan["deposit_day"], plan["buy_hour"])
    spent = _spent(orders, since)
    amount = Decimal(str(plan["amount"]))
    limit = _decimal(plan["monthly_limit"])
    plan_timing = strategy.timing(
        mode=plan["mode"],
        now=now,
        deposit_day=plan["deposit_day"],
        buy_hour=plan["buy_hour"],
        end_date=date.fromisoformat(plan["end_date"]) if plan["end_date"] else None,
        balance=free - spent,  # dry runs spend the real balance on paper
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
    )
    message = decision.message
    bought = False
    if decision.action == "buy":
        cost = _cents(decision.amount * price)
        await client.create(
            "dca.order",
            {
                "owner": plan["owner"],
                "plan": plan["sys_id"],
                "pair": pair.name,
                "strategy": decision.strategy,
                "state": "dry_run",
                "amount": str(decision.amount),
                "price": str(price),
                "cost": str(cost),
                "fee": str((cost * FEE).quantize(Decimal("0.0001"))),
                "currency": pair.currency,
                "placed_at": now.isoformat(),
            },
        )
        cur = pair.currency
        said = f"{decision.amount} {pair.asset} at {price:.2f} {cur} ({cost} {cur})"
        message = f"Dry run: would have bought {said}."
        await client.notify(
            "dca.buy",
            f"Dry run: {plan['name']}",
            people=[plan["owner"]],
            message=f"Would have bought {said}.",
            link=f"/form/dca.plan/{plan['sys_id']}",
        )
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


def _short(error: Exception) -> str:
    if isinstance(error, (KrakenError, Problem)):
        return str(error.title if isinstance(error, Problem) else error)
    return f"{type(error).__name__}: {error}"[:400]


async def run(job: JobRun) -> str:
    client = Client(job.records)
    found = await client.list(
        "dca.plan", where={"field": "active", "op": "eq", "value": True}, sort=["created"]
    )
    checked = bought = 0
    for plan in found["records"]:
        checked += 1
        try:
            _, did = await check_plan(client, plan, job.now)
            bought += did
        except (KrakenError, Problem, httpx.HTTPError, OSError, ValueError, KeyError) as error:
            await _problem(client, plan, _short(error), job.now)
    return f"{checked} plan(s) checked, {bought} dry run(s)."


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
