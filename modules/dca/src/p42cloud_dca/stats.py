"""What a plan's orders add up to: plain arithmetic, no database and no Kraken."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class Buy:
    when: datetime
    amount: Decimal
    price: Decimal
    cost: Decimal
    fee: Decimal
    strategy: str


@dataclass(frozen=True)
class Position:
    buys: int
    amount: Decimal
    invested: Decimal
    fees: Decimal

    @property
    def average_price(self) -> Decimal | None:
        return self.invested / self.amount if self.amount else None

    def value(self, price: Decimal) -> Decimal:
        return self.amount * price

    def profit(self, price: Decimal) -> Decimal:
        """What it's worth now, less what it cost and its fees."""
        return self.value(price) - self.invested - self.fees

    def profit_percent(self, price: Decimal) -> Decimal | None:
        return self.profit(price) / self.invested * 100 if self.invested else None


def position(buys: list[Buy]) -> Position:
    return Position(
        buys=len(buys),
        amount=sum((b.amount for b in buys), Decimal(0)),
        invested=sum((b.cost for b in buys), Decimal(0)),
        fees=sum((b.fee for b in buys), Decimal(0)),
    )


@dataclass(frozen=True)
class Step:
    """After each buy: what's held, what's invested, the average price so far."""

    when: datetime
    price: Decimal
    holdings: Decimal
    invested: Decimal
    average: Decimal


def steps(buys: list[Buy]) -> list[Step]:
    found: list[Step] = []
    holdings = invested = Decimal(0)
    for buy in sorted(buys, key=lambda b: b.when):
        holdings += buy.amount
        invested += buy.cost
        found.append(
            Step(
                buy.when,
                buy.price,
                holdings,
                invested,
                invested / holdings if holdings else Decimal(0),
            )
        )
    return found


def per_month(
    buys: list[Buy], months: int = 12, now: datetime | None = None
) -> list[tuple[str, Decimal]]:
    """Invested per month, the last `months` months, oldest first (months without buys: 0)."""
    now = now or datetime.now(UTC)
    keys: list[str] = []
    year, month = now.year, now.month
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    totals = dict.fromkeys(keys, Decimal(0))
    for buy in buys:
        key = f"{buy.when.year:04d}-{buy.when.month:02d}"
        if key in totals:
            totals[key] += buy.cost
    return [(k, totals[k]) for k in reversed(keys)]


def by_strategy(buys: list[Buy]) -> Counter[str]:
    return Counter(b.strategy for b in buys)


def profit_by_day(
    buys: list[Buy], closes: list[tuple[datetime, Decimal]], price: Decimal | None, now: datetime
) -> list[tuple[datetime, Decimal]]:
    """Profit or loss at each day's close since the first buy, today at the current price."""
    ordered = sorted(buys, key=lambda b: b.when)
    points: list[tuple[datetime, Decimal]] = []
    for day, close in sorted(closes):
        held = [b for b in ordered if b.when < day + timedelta(days=1)]
        if held and day.date() < now.date():
            points.append((day, position(held).profit(close)))
    if price is not None and ordered:
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        points.append((today, position(ordered).profit(price)))
    return points


@dataclass(frozen=True)
class Targets:
    profit_percent: Decimal | None = None
    profit: Decimal | None = None
    loss_percent: Decimal | None = None  # entered positive: notify at minus this
    loss: Decimal | None = None


def reached(targets: Targets, at: Position, price: Decimal) -> set[str]:
    """The targets reached at this price."""
    if not at.invested or not at.amount:
        return set()
    profit = at.profit(price)
    percent = at.profit_percent(price) or Decimal(0)
    found: set[str] = set()
    if targets.profit_percent is not None and percent >= targets.profit_percent:
        found.add("profit_percent")
    if targets.profit is not None and profit >= targets.profit:
        found.add("profit")
    if targets.loss_percent is not None and percent <= -targets.loss_percent:
        found.add("loss_percent")
    if targets.loss is not None and profit <= -targets.loss:
        found.add("loss")
    return found
