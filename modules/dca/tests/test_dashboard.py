"""The dashboard's routes for one plan, as its owner; nobody else's plan; targets told once."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from p42cloud.sdk import Problem
from p42cloud.sdk.testing import CallRoute, RunJob, StandIn

from p42cloud_dca.kraken import TYPE
from p42cloud_dca.widgets import SHOWN
from tests.helpers import Acting, connect, investor, new_plan
from tests.test_cycle import Kraken


@pytest.fixture
def kraken(stand_in: StandIn) -> Kraken:
    made = Kraken()
    stand_in(TYPE, made)
    return made


async def _with_orders(as_system: Acting, as_person: Acting, **plan: Any) -> tuple[str, str]:
    ivy = await investor(as_system)
    await connect(as_person, ivy)
    made = await new_plan(as_person, ivy, **plan)
    async with as_system() as client:  # orders as the cycle would have made them
        for n, (price, cost) in enumerate((("80000", "80"), ("70000", "140"))):
            await client.create(
                "dca.order",
                {
                    "owner": ivy,
                    "plan": made["sys_id"],
                    "pair": "XBTCHF",
                    "strategy": "scheduled",
                    "state": "filled",
                    "amount": "0.001" if n == 0 else "0.002",
                    "price": price,
                    "cost": cost,
                    "fee": "0.3",
                    "currency": "CHF",
                    "placed_at": (datetime.now(UTC) - timedelta(days=20 - n)).isoformat(),
                },
            )
    return ivy, made["sys_id"]


async def test_every_widget_answers_for_its_owners_plan(
    as_system: Acting, as_person: Acting, call_route: CallRoute, kraken: Kraken
) -> None:
    ivy, plan = await _with_orders(as_system, as_person)
    for shown in SHOWN:
        answer = await call_route(ivy, "GET", f"/dca/widget/{shown.name}", query={"plan": plan})
        assert answer["subtitle"].startswith("Bitcoin · real orders"), shown.name
    invested = await call_route(ivy, "GET", "/dca/widget/invested", query={"plan": plan})
    profit = await call_route(ivy, "GET", "/dca/widget/profit", query={"plan": plan})
    by_type = await call_route(ivy, "GET", "/dca/widget/buys_by_type", query={"plan": plan})
    assert invested["value"] == 220 and invested["unit"] == "CHF"
    assert profit["value"] == pytest.approx(240 - 220 - 0.6)  # 0.003 at 80'000
    assert by_type["points"] == [{"label": "Planned", "value": 2.0, "key": "Planned"}]


async def test_nobody_else_sees_a_plans_figures(
    as_system: Acting, as_person: Acting, call_route: CallRoute, kraken: Kraken
) -> None:
    _, plan = await _with_orders(as_system, as_person)
    stranger = await investor(as_system)
    with pytest.raises(Problem):
        await call_route(stranger, "GET", "/dca/widget/invested", query={"plan": plan})
    with pytest.raises(Problem) as unchosen:
        await call_route(stranger, "GET", "/dca/widget/invested")
    assert unchosen.value.status == 422


async def test_a_target_is_told_once_until_it_is_left(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: Kraken
) -> None:
    ivy, plan = await _with_orders(as_system, as_person, target_profit="10")
    await run_job("dca.cycle")
    kraken.price = "90000.0"  # worth 270: 49.4 after fees
    await run_job("dca.cycle")
    await run_job("dca.cycle")
    async with as_system() as client:
        told = (
            await client.list(
                "sys.notification",
                where={
                    "and": [
                        {"field": "person", "op": "eq", "value": ivy},
                        {"field": "kind", "op": "eq", "value": "dca.target"},
                    ]
                },
            )
        )["records"]
        hit = (await client.get("dca.plan", plan))["targets_hit"]
    assert len(told) == 1 and told[0]["title"].startswith("Profit target reached: Bitcoin")
    assert hit == ["profit"]
