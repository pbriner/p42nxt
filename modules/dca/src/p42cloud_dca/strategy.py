"""When and how much a plan buys: plain rules, no database and no Kraken.

Times are UTC. Money and amounts are Decimals; nothing here is rounded except where Kraken's
rules say so (the volume down to the pair's lot decimals).
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Literal

STALE = timedelta(hours=48)  # a planned buy missed by longer is planned again, not made
DEFAULT_COOLDOWN = Decimal(24)
# Kept free when a buy is cut to the balance: the fee, and the price moving before the market
# order fills, so Kraken doesn't refuse it for want of money.
MARGIN = Decimal("0.01")


def _boundary(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _months(year: int, month: int, add: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + add
    return index // 12, index % 12 + 1


def boundary_after(moment: datetime, day: int, hour: int) -> datetime:
    """The first deposit-day boundary (day at hour) strictly after the moment."""
    this = _boundary(moment.year, moment.month, day, hour)
    if this > moment:
        return this
    year, month = _months(moment.year, moment.month, 1)
    return _boundary(year, month, day, hour)


def period_start(moment: datetime, day: int, hour: int) -> datetime:
    """The last deposit-day boundary at or before the moment: where the monthly limit counts."""
    this = _boundary(moment.year, moment.month, day, hour)
    if this <= moment:
        return this
    year, month = _months(moment.year, moment.month, -1)
    return _boundary(year, month, day, hour)


@dataclass(frozen=True)
class Timing:
    period_end: datetime | None
    buys_left: int
    next_buy: datetime | None
    reason: str


def timing(  # noqa: PLR0913 - the plan's settings and the market, by keyword
    *,
    mode: Literal["recurring", "lump_sum"],
    now: datetime,
    deposit_day: int,
    buy_hour: int,
    end_date: date | None,
    balance: Decimal,
    price: Decimal,
    amount: Decimal,
    monthly_limit: Decimal | None,
    spent: Decimal,
) -> Timing:
    """Spreads the balance evenly until the period ends: the deposit day (recurring) or the end
    date (lump sum). The last planned buy lands on the period's end."""
    if mode == "lump_sum":
        if end_date is None:
            return Timing(None, 0, None, "A lump sum plan needs an end date.")
        end = datetime.combine(end_date, time(23, 59, 59), tzinfo=UTC)
        if now >= end:
            return Timing(end, 0, None, "The end date is reached.")
    else:
        end = boundary_after(now, deposit_day, buy_hour)
    cost = amount * price
    if balance <= 0:
        return Timing(end, 0, None, "No balance on Kraken.")
    buys = math.floor(balance / cost) if cost > 0 else 0
    if buys <= 0:
        return Timing(end, 0, None, f"The balance doesn't cover one buy ({cost:.2f}).")
    if monthly_limit is not None:
        remaining = max(Decimal(0), monthly_limit - spent)
        buys = min(buys, math.floor(remaining / cost) if cost > 0 else 0)
        if buys <= 0:
            return Timing(end, 0, None, "The monthly limit is reached.")
    spacing = (end - now) / buys
    return Timing(end, buys, now + spacing, f"{buys} buys left until {end:%d.%m.%Y}.")


@dataclass(frozen=True)
class Tier:
    threshold_percent: Decimal
    amount: Decimal
    enabled: bool = True


@dataclass(frozen=True)
class TierChoice:
    amount: Decimal
    change_percent: Decimal | None  # None: no reference to compare with
    tier: Tier | None


def choose_tier(
    tiers: list[Tier], base: Decimal, price: Decimal, reference: Decimal | None
) -> TierChoice:
    """A dip tier (below 0) counts once the price is that far below the reference or further,
    the deepest reached wins; a rise tier (0 or more) once it's that far above, the highest
    reached wins; dips first; otherwise the base amount."""
    if reference is None or reference <= 0:
        return TierChoice(base, None, None)
    change = (price - reference) / reference * 100
    on = [t for t in tiers if t.enabled]
    dips = [t for t in on if t.threshold_percent < 0 and change <= t.threshold_percent]
    if dips:
        deepest = min(dips, key=lambda t: t.threshold_percent)
        return TierChoice(deepest.amount, change, deepest)
    rises = [t for t in on if t.threshold_percent >= 0 and change >= t.threshold_percent]
    if rises:
        highest = max(rises, key=lambda t: t.threshold_percent)
        return TierChoice(highest.amount, change, highest)
    return TierChoice(base, change, None)


@dataclass(frozen=True)
class Market:
    """Kraken's rules for a pair."""

    ordermin: Decimal  # the smallest volume
    lot_decimals: int  # the volume's decimals
    costmin: Decimal = Decimal(0)  # the smallest cost in the quote currency


def volume(amount: Decimal, market: Market) -> Decimal:
    """The amount as Kraken takes it: down to the pair's decimals."""
    step = Decimal(1).scaleb(-market.lot_decimals)
    return amount.quantize(step, rounding=ROUND_DOWN)


@dataclass(frozen=True)
class Decision:
    """What a check does: nothing, skip a planned buy, or buy (a dry run in 6a)."""

    action: Literal["wait", "skip", "buy"]
    strategy: Literal["scheduled", "dynamic"] | None
    amount: Decimal
    next_buy: datetime | None  # what the plan keeps as its next buy afterwards
    message: str


def _covered(amount: Decimal, price: Decimal, balance: Decimal | None) -> tuple[Decimal, str]:
    """The amount the free balance pays for (the margin kept free), and what to say if it's cut."""
    if balance is None or amount * price * (1 + MARGIN) <= balance:
        return amount, ""
    if balance <= 0 or price <= 0:
        return Decimal(0), ""
    return balance / (price * (1 + MARGIN)), f" Only {balance:.2f} left."


def decide(  # noqa: PLR0911, PLR0913 - one rule after another, by keyword
    *,
    now: datetime,
    planned: datetime | None,
    plan_timing: Timing,
    base: Decimal,
    choice: TierChoice,
    dynamic: bool,
    cooled_down: bool,
    price: Decimal,
    market: Market,
    max_price: Decimal | None,
    monthly_limit: Decimal | None,
    spent: Decimal,
    balance: Decimal | None = None,
) -> Decision:
    """The planned next buy is kept until it's due (sticky), so the time people see is the time
    it buys; an extra buy happens when a tier asks for more than the base, once cooled down. A
    buy the free balance doesn't cover buys what the balance does."""
    if planned is not None and now - planned > STALE:
        return Decision(
            "wait",
            None,
            Decimal(0),
            None,
            f"A buy planned for {planned:%d.%m.%Y %H:%M} UTC was missed; planning again.",
        )
    if plan_timing.next_buy is None and planned is None:
        return Decision("wait", None, Decimal(0), None, plan_timing.reason)
    keep = planned or plan_timing.next_buy
    scheduled = keep is not None and keep <= now
    amount = choice.amount if dynamic else base
    extra = dynamic and not scheduled and amount > base and cooled_down
    if not scheduled and not extra:
        return Decision(
            "wait",
            None,
            Decimal(0),
            keep,
            f"Next buy {keep:%d.%m.%Y %H:%M} UTC ({plan_timing.reason.rstrip('.')})."
            if keep is not None
            else plan_timing.reason,
        )
    strategy: Literal["scheduled", "dynamic"] = "scheduled" if scheduled else "dynamic"
    after = None if scheduled else keep  # a scheduled buy uses up its slot; an extra doesn't
    if amount <= 0:
        change = f"{choice.change_percent:+.1f}" if choice.change_percent is not None else "?"
        return Decision(
            "skip", strategy, Decimal(0), after, f"Skipped: the tier for {change} % says skip."
        )
    amount, left = _covered(amount, price, balance)
    if amount <= 0:
        return Decision("skip", strategy, Decimal(0), after, "Skipped: no balance on Kraken.")
    amount = volume(amount, market)
    if amount < market.ordermin:
        return Decision(
            "skip",
            strategy,
            amount,
            after,
            f"Skipped: {amount} is below Kraken's minimum {market.ordermin}." + left,
        )
    if max_price is not None and price > max_price:
        return Decision(
            "skip",
            strategy,
            amount,
            after,
            f"Skipped: {price:.2f} is above the max price {max_price:.2f}.",
        )
    cost = amount * price
    if cost < market.costmin:
        return Decision(
            "skip",
            strategy,
            amount,
            after,
            f"Skipped: {cost:.2f} is below Kraken's smallest order cost {market.costmin}." + left,
        )
    if monthly_limit is not None and spent + cost > monthly_limit:
        return Decision(
            "skip",
            strategy,
            amount,
            after,
            f"Skipped: {cost:.2f} would pass the monthly limit ({spent:.2f} spent).",
        )
    return Decision("buy", strategy, amount, after, left.strip())
