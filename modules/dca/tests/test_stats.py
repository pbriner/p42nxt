"""What orders add up to: position, profit, steps, months, profit by day, targets."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from p42cloud_dca import stats
from p42cloud_dca.stats import Buy, Targets


def day(n: int) -> datetime:
    return datetime(2026, 9, 1, 8, tzinfo=UTC) + timedelta(days=n)


BUYS = [
    Buy(day(0), D("0.001"), D(80000), D(80), D("0.2"), "scheduled"),
    Buy(day(10), D("0.002"), D(70000), D(140), D("0.4"), "dynamic"),
]


def test_a_position_adds_up_its_buys() -> None:
    at = stats.position(BUYS)
    assert (at.buys, at.amount, at.invested, at.fees) == (2, D("0.003"), D(220), D("0.6"))
    assert at.average_price == D(220) / D("0.003")
    assert at.value(D(90000)) == D(270)
    assert at.profit(D(90000)) == D("49.4")  # 270 - 220 - 0.6
    assert round(at.profit_percent(D(90000)) or 0, 2) == D("22.45")


def test_steps_follow_holdings_and_the_average() -> None:
    found = stats.steps(list(reversed(BUYS)))
    assert [s.holdings for s in found] == [D("0.001"), D("0.003")]
    assert found[-1].average == D(220) / D("0.003")


def test_invested_per_month_has_every_month() -> None:
    months = stats.per_month(BUYS, 3, now=datetime(2026, 10, 5, tzinfo=UTC))
    assert months == [("2026-08", D(0)), ("2026-09", D(220)), ("2026-10", D(0))]


def test_profit_by_day_counts_what_was_held_then_and_ends_today() -> None:
    closes = [(day(n).replace(hour=0), D(75000)) for n in range(12)]
    now = day(20)
    points = stats.profit_by_day(BUYS, closes, D(90000), now)
    assert points[0] == (day(0).replace(hour=0), D("0.001") * 75000 - 80 - D("0.2"))
    assert points[-1] == (now.replace(hour=0), D("49.4"))
    assert len(points) == 13  # 12 days with holdings, and today


def test_targets_are_reached_by_profit_or_loss() -> None:
    at = stats.position(BUYS)
    targets = Targets(profit_percent=D(20), profit=D(100), loss_percent=D(10), loss=D(30))
    assert stats.reached(targets, at, D(90000)) == {"profit_percent"}
    assert stats.reached(targets, at, D(60000)) == {"loss_percent", "loss"}  # -40.6, -18.5 %
    assert stats.reached(targets, stats.position([]), D(1)) == set()
