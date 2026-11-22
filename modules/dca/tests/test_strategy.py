"""The buying rules on their own: periods, spreading, tiers, and what each check decides."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from p42cloud_dca import strategy
from p42cloud_dca.strategy import Market, Tier, Timing, boundary_after, decide, period_start

BTC = Market(ordermin=D("0.00005"), lot_decimals=8, costmin=D("0.5"))
NOW = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)


def at(day: int, hour: int, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(14, 10), at(24, 8)),
        (at(24, 7), at(24, 8)),
        (at(24, 8), at(24, 8, 12)),  # in the boundary hour: the next month's, not now
        (at(24, 8) + timedelta(minutes=5), at(24, 8, 12)),
        (datetime(2026, 12, 30, tzinfo=UTC), datetime(2027, 1, 24, 8, tzinfo=UTC)),
    ],
)
def test_the_period_ends_at_the_next_deposit_day(now: datetime, expected: datetime) -> None:
    assert boundary_after(now, 24, 8) == expected


def test_the_monthly_limit_counts_from_the_last_deposit_day() -> None:
    assert period_start(at(14, 10), 24, 8) == at(24, 8, 10)
    assert period_start(at(24, 8), 24, 8) == at(24, 8)
    assert period_start(datetime(2026, 1, 3, tzinfo=UTC), 24, 8) == datetime(
        2025, 12, 24, 8, tzinfo=UTC
    )


def _timing(**changes: object) -> Timing:
    given: dict[str, object] = {
        "mode": "recurring",
        "now": NOW,
        "deposit_day": 24,
        "buy_hour": 8,
        "end_date": None,
        "balance": D(1000),
        "price": D(80000),
        "amount": D("0.001"),  # 80 a buy
        "monthly_limit": None,
        "spent": D(0),
    } | changes
    return strategy.timing(**given)  # type: ignore[arg-type]


def test_the_balance_is_spread_until_the_deposit_day() -> None:
    found = _timing()
    assert found.buys_left == 12  # 1000 / 80
    assert found.period_end == at(24, 8)
    assert found.next_buy == NOW + (at(24, 8) - NOW) / 12
    assert found.reason == "12 buys left until 24.11.2026."


def test_a_lump_sum_is_spread_until_its_end_date() -> None:
    found = _timing(mode="lump_sum", end_date=date(2026, 11, 16))
    assert found.period_end == datetime(2026, 11, 16, 23, 59, 59, tzinfo=UTC)
    assert _timing(mode="lump_sum", end_date=None).reason == "A lump sum plan needs an end date."
    assert _timing(mode="lump_sum", end_date=date(2026, 11, 13)).next_buy is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"balance": D(0)}, "No balance on Kraken."),
        ({"balance": D(50)}, "The balance doesn't cover one buy (80.00)."),
        ({"monthly_limit": D(100), "spent": D(40)}, "The monthly limit is reached."),
    ],
)
def test_no_buy_when_money_or_the_limit_runs_out(changes: dict[str, object], reason: str) -> None:
    found = _timing(**changes)
    assert (found.next_buy, found.reason) == (None, reason)


def test_the_monthly_limit_caps_the_buys() -> None:
    assert _timing(monthly_limit=D(400), spent=D(150)).buys_left == 3  # 250 left / 80


TIERS = [
    Tier(D(-10), D("0.003")),
    Tier(D(-5), D("0.002")),
    Tier(D(10), D(0)),
    Tier(D(20), D("0.0005")),
]


@pytest.mark.parametrize(
    ("price", "amount", "tier"),
    [
        (D(100), D("0.001"), None),  # no change: the base amount
        (D(97), D("0.001"), None),  # -3 %: no dip tier reached
        (D(95), D("0.002"), D(-5)),  # exactly -5 %
        (D(80), D("0.003"), D(-10)),  # -20 %: the deepest reached
        (D(112), D(0), D(10)),  # +12 %: skip
        (D(125), D("0.0005"), D(20)),  # +25 %: the highest reached
    ],
)
def test_tiers_pick_the_deepest_dip_or_the_highest_rise(
    price: D, amount: D, tier: D | None
) -> None:
    found = strategy.choose_tier(TIERS, D("0.001"), price, D(100))
    assert found.amount == amount
    assert (found.tier.threshold_percent if found.tier else None) == tier


def test_tiers_that_are_off_or_without_a_reference_dont_count() -> None:
    off = [Tier(D(-5), D("0.002"), enabled=False)]
    assert strategy.choose_tier(off, D("0.001"), D(90), D(100)).amount == D("0.001")
    assert strategy.choose_tier(TIERS, D("0.001"), D(90), None).change_percent is None


def _decide(**changes: object) -> strategy.Decision:
    plan_timing = _timing()
    given: dict[str, object] = {
        "now": NOW,
        "planned": None,
        "plan_timing": plan_timing,
        "base": D("0.001"),
        "choice": strategy.TierChoice(D("0.001"), None, None),
        "dynamic": False,
        "cooled_down": True,
        "price": D(80000),
        "market": BTC,
        "max_price": None,
        "monthly_limit": None,
        "spent": D(0),
    } | changes
    return decide(**given)  # type: ignore[arg-type]


def test_the_planned_buy_waits_until_its_time_and_keeps_its_time() -> None:
    first = _decide()
    assert first.action == "wait" and first.next_buy == _timing().next_buy
    later = NOW + timedelta(minutes=5)
    kept = _decide(now=later, planned=first.next_buy, plan_timing=_timing(now=later))
    assert kept.next_buy == first.next_buy  # sticky: the time people see is when it buys
    assert kept.message.startswith("Next buy ")


def test_a_due_buy_buys_and_uses_up_its_slot() -> None:
    due = _decide(planned=NOW - timedelta(minutes=1))
    assert (due.action, due.strategy, due.amount, due.next_buy) == (
        "buy",
        "scheduled",
        D("0.001"),
        None,
    )


def test_a_buy_missed_long_ago_is_planned_again_not_made() -> None:
    stale = _decide(planned=NOW - timedelta(hours=49))
    assert stale.action == "wait" and stale.next_buy is None
    assert "was missed" in stale.message


def test_an_extra_buy_needs_a_bigger_tier_and_a_cooldown() -> None:
    bigger = strategy.TierChoice(D("0.002"), D(-6), TIERS[1])
    planned = NOW + timedelta(hours=10)
    extra = _decide(dynamic=True, choice=bigger, planned=planned)
    assert (extra.action, extra.strategy, extra.amount, extra.next_buy) == (
        "buy",
        "dynamic",
        D("0.002"),
        planned,  # the planned buy stays
    )
    assert _decide(dynamic=True, choice=bigger, planned=planned, cooled_down=False).action == "wait"
    smaller = strategy.TierChoice(D("0.0005"), D(25), TIERS[3])
    assert _decide(dynamic=True, choice=smaller, planned=planned).action == "wait"


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        (
            {"dynamic": True, "choice": strategy.TierChoice(D(0), D("12.3"), TIERS[2])},
            "Skipped: the tier for +12.3 % says skip.",
        ),
        (
            {"base": D("0.00001"), "choice": strategy.TierChoice(D("0.00001"), None, None)},
            "Skipped: 0.00001000 is below Kraken's minimum 0.00005.",
        ),
        ({"max_price": D(70000)}, "Skipped: 80000.00 is above the max price 70000.00."),
        (
            {"monthly_limit": D(100), "spent": D(40)},
            "Skipped: 80.00 would pass the monthly limit (40.00 spent).",
        ),
    ],
)
def test_a_due_buy_is_skipped_for_good_reasons(changes: dict[str, object], says: str) -> None:
    skipped = _decide(planned=NOW - timedelta(minutes=1), **changes)
    assert (skipped.action, skipped.message, skipped.next_buy) == ("skip", says, None)


def test_volumes_go_down_to_the_pairs_decimals() -> None:
    eth = Market(ordermin=D("0.002"), lot_decimals=4)
    assert strategy.volume(D("0.0123456"), eth) == D("0.0123")


def test_a_buy_the_balance_doesnt_cover_buys_what_it_does() -> None:
    bigger = strategy.TierChoice(D("0.002"), D(-6), TIERS[1])
    planned = NOW + timedelta(hours=10)
    # The tier asks for 160.00; 101.00 buys 0.00125 at 80,000 with the margin kept free.
    cut = _decide(dynamic=True, choice=bigger, planned=planned, balance=D(101))
    assert (cut.action, cut.amount, cut.message) == ("buy", D("0.00125"), "Only 101.00 left.")
    assert cut.amount * D(80000) * (1 + strategy.MARGIN) <= D(101)
    enough = _decide(dynamic=True, choice=bigger, planned=planned, balance=D(1000))
    assert (enough.amount, enough.message) == (D("0.002"), "")


def test_too_little_balance_skips() -> None:
    due = NOW - timedelta(minutes=1)
    little = _decide(planned=due, balance=D(3))
    assert little.action == "skip"
    assert little.message == (
        "Skipped: 0.00003712 is below Kraken's minimum 0.00005. Only 3.00 left."
    )
    assert _decide(planned=due, balance=D(0)).message == "Skipped: no balance on Kraken."
