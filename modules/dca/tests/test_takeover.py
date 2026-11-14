"""Taking over plans, tiers and orders from the previous installation's export."""

import uuid
from typing import Any

import pytest
from p42cloud.sdk import Problem
from p42cloud.sdk.testing import RunJob

from tests.helpers import Acting, investor, read


def _export() -> dict[str, Any]:
    plan, other = str(uuid.uuid4()), str(uuid.uuid4())
    return {
        "plans": [
            {
                "sys_id": plan,
                "name": "BTC (old)",
                "pair": "XBTCHF",
                "mode": "recurring",
                "amount": "0.00010000",
                "deposit_day": 24,
                "buy_hour": 8,
                "dynamic": True,
                "reference": "last_buy",
                "monthly_limit": "500.00",
                "target_profit_percent": "20.00",
                "active": True,
                "live": True,
            },
            {"sys_id": other, "name": "Odd", "pair": "DOGEUSD", "amount": "1"},
        ],
        "tiers": [
            {"plan": plan, "threshold_percent": "-5.00", "amount": "0.0002", "enabled": True}
        ],
        "orders": [
            {
                "sys_id": str(uuid.uuid4()),
                "plan": plan,
                "pair": "XBTCHF",
                "strategy": "scheduled",
                "state": "filled",
                "amount": "0.00010000",
                "price": "80123.40",
                "cost": "8.01",
                "fee": "0.0208",
                "currency": "CHF",
                "userref": 123456,
                "kraken_txid": f"O{uuid.uuid4().hex[:5].upper()}-AAAAA-BBBBBB",
                "placed_at": "2026-03-01T08:00:00+00:00",
                "created": "2026-11-08T10:00:00+00:00",
            },
            {
                "sys_id": str(uuid.uuid4()),
                "plan": plan,
                "pair": "XBTCHF",
                "strategy": "dip",
                "state": "dry_run",
                "amount": "0.0002",
                "price": "70000",
                "cost": "14",
                "fee": "0.0364",
                "currency": "CHF",
                "placed_at": "2026-04-01T08:00:00+00:00",
            },
            {"sys_id": str(uuid.uuid4()), "plan": plan, "state": "weird", "amount": "1"},
        ],
    }


async def _take_over(as_person: Acting, run_job: RunJob, person: str, data: Any) -> dict[str, Any]:
    async with as_person(person) as client:
        made = await client.create("dca.takeover", {"data": data})
    await run_job("dca.take_over")
    async with as_person(person) as client:
        return await client.get("dca.takeover", made["sys_id"])


async def test_plans_and_orders_are_taken_over_once_and_dont_buy(
    as_system: Acting, as_person: Acting, run_job: RunJob
) -> None:
    ivy = await investor(as_system)
    export = _export()
    done = await _take_over(as_person, run_job, ivy, export)
    assert done["state"] == "done", done["message"]
    assert done["message"] == "1 plan(s) with 1 tier(s) and 2 order(s) taken over; 2 skipped."
    assert done["data"] is None  # the export isn't kept
    (plan,) = await read(as_person, ivy, "dca.plan")
    assert (plan["name"], plan["owner"], plan["active"], plan["live"]) == (
        "BTC (old)",
        ivy,
        False,
        False,
    )
    assert plan["pair"] == "xbtchf" and plan["monthly_limit"] is not None
    orders = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert sorted(o["state"] for o in orders) == ["dry_run", "filled"]
    filled = next(o for o in orders if o["state"] == "filled")
    assert filled["placed_at"].startswith("2026-03-01") and filled["kraken_txid"]
    assert next(o for o in orders if o["state"] == "dry_run")["strategy"] == "manual"
    assert len(await read(as_person, ivy, "dca.tier", plan=plan["sys_id"])) == 1

    again = await _take_over(as_person, run_job, ivy, export)
    assert again["message"] == "0 plan(s) with 0 tier(s) and 0 order(s) taken over; 2 skipped."
    assert len(await read(as_person, ivy, "dca.order", plan=plan["sys_id"])) == 2


async def test_a_takeover_is_its_makers_and_needs_an_export(
    as_system: Acting, as_person: Acting, run_job: RunJob
) -> None:
    ivy, max_ = await investor(as_system), await investor(as_system)
    await _take_over(as_person, run_job, ivy, _export())
    assert await read(as_person, max_, "dca.plan") == []
    assert await read(as_person, max_, "dca.takeover") == []
    async with as_person(ivy) as client:
        with pytest.raises(Problem) as refused:
            await client.create("dca.takeover", {"data": ["not", "an", "object"]})
        mine = await client.create("dca.plan", {"name": "X", "amount": "1", "source_ref": "x"})
    assert [e.field for e in refused.value.errors] == ["data"]
    assert mine["source_ref"] is None  # people never say where a plan came from


async def test_a_long_history_is_taken_over_whole_and_only_once(
    as_system: Acting, as_person: Acting, run_job: RunJob
) -> None:
    ivy = await investor(as_system)
    export = _export()
    plan = export["plans"][0]["sys_id"]
    export["orders"] = [
        {
            "sys_id": str(uuid.uuid4()),
            "plan": plan,
            "pair": "XBTCHF",
            "strategy": "scheduled",
            "state": "filled",
            "amount": "0.0001",
            "price": "80000",
            "cost": "8",
            "fee": "0.02",
            "currency": "CHF",
            "placed_at": f"2025-{1 + n // 60:02d}-{1 + n % 28:02d}T{n % 24:02d}:00:00+00:00",
        }
        for n in range(620)  # more than one page
    ]
    first = await _take_over(as_person, run_job, ivy, export)
    again = await _take_over(as_person, run_job, ivy, export)
    async with as_person(ivy) as client:
        found = await client.list("dca.order", limit=1)
    assert first["message"].startswith("1 plan(s) with 1 tier(s) and 620 order(s)")
    assert again["message"].startswith("0 plan(s) with 0 tier(s) and 0 order(s)")
    assert found["total"] == 620
