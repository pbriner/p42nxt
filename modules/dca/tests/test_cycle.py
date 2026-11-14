"""The cycle against a stand-in Kraken: plans checked, dry runs recorded, problems told once."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from p42cloud.sdk import Client, Problem
from p42cloud.sdk.testing import RunJob, StandIn

from p42cloud_dca.kraken import TYPE, sign

Acting = Callable[..., AbstractAsyncContextManager[Client]]


class Kraken:
    """Answers like Kraken's API; remembers what it was asked."""

    def __init__(self) -> None:
        self.price = "80000.0"
        self.free = "1000.0"
        self.down = False
        self.asked: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if self.down:
            return httpx.Response(200, json={"error": ["EService:Unavailable"]})
        path = request.url.path
        if path == "/0/public/Ticker":
            result: Any = {"XXBTZCHF": {"c": [self.price, "0.1"]}}
        elif path == "/0/public/AssetPairs":
            result = {"XXBTZCHF": {"ordermin": "0.00005", "lot_decimals": 8, "costmin": "0.5"}}
        elif path == "/0/private/BalanceEx":
            result = {"ZCHF": {"balance": self.free, "hold_trade": "0"}}
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={"error": [], "result": result})


@pytest.fixture
def kraken(stand_in: StandIn) -> Kraken:
    made = Kraken()
    stand_in(TYPE, made)
    return made


async def _investor(as_system: Acting) -> str:
    """Someone holding the DCA investor role, with a Kraken connection."""
    async with as_system() as client:
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "dca.user"}
        )
        group = await client.create("sys.group", {"name": f"Investors {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        person = await client.create("sys.person", {"name": f"Ivy {uuid.uuid4().hex[:4]}"})
        await client.create(
            "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
        )
    return person["sys_id"]


async def _connect(as_person: Acting, person: str) -> None:
    async with as_person(person) as client:
        made = await client.create("sys.connection", {"type": TYPE, "label": "Kraken"})
        for name, value in (("api_key", "key"), ("api_secret", "c2VjcmV0")):
            await client.create(
                "sys.connection_secret",
                {"connection": made["sys_id"], "name": name, "value": value},
            )


async def _plan(as_person: Acting, person: str, **values: Any) -> dict[str, Any]:
    async with as_person(person) as client:
        return await client.create(
            "dca.plan", {"name": "Bitcoin", "pair": "xbtchf", "amount": "0.001"} | values
        )


async def _read(as_person: Acting, person: str, table: str, **where: Any) -> list[dict[str, Any]]:
    async with as_person(person) as client:
        found = await client.list(
            table,
            where={"and": [{"field": k, "op": "eq", "value": v} for k, v in where.items()]}
            if where
            else None,
        )
    return found["records"]


async def test_a_plan_is_planned_then_buys_a_dry_run_when_due(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: Kraken
) -> None:
    ivy = await _investor(as_system)
    await _connect(as_person, ivy)
    plan = await _plan(as_person, ivy)
    assert plan["owner"] == ivy and "next check" in plan["last_message"]

    state, said = await run_job("dca.cycle")
    (planned,) = await _read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert state == "succeeded" and said is not None and "checked" in said
    assert planned["next_buy"] is not None and planned["planned_buys"] == 12
    assert Decimal(planned["last_price"]) == 80000 and planned["last_message"].startswith(
        "Next buy"
    )

    async with as_system() as client:  # as if its time had come
        await client.update(
            "dca.plan",
            plan["sys_id"],
            {"next_buy": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            planned["_etag"],
        )
    await run_job("dca.cycle")
    orders = await _read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    (after,) = await _read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert len(orders) == 1
    assert (orders[0]["state"], orders[0]["strategy"]) == ("dry_run", "scheduled")
    assert Decimal(orders[0]["cost"]) == Decimal("80.00") and orders[0]["owner"] == ivy
    assert after["last_message"].startswith("Dry run: would have bought 0.00100000 BTC")


async def test_private_calls_are_signed_the_kraken_way(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: Kraken
) -> None:
    ivy = await _investor(as_system)
    await _connect(as_person, ivy)
    await _plan(as_person, ivy)
    await run_job("dca.cycle")
    private = [r for r in kraken.asked if r.url.path.startswith("/0/private/")]
    assert private and private[-1].headers["API-Key"] == "key"
    again = httpx.Request("POST", private[-1].url, content=private[-1].content)
    sign(again, {"api_key": "key", "api_secret": "c2VjcmV0"})
    assert again.headers["API-Sign"] == private[-1].headers["API-Sign"]
    assert "nonce" in parse_qs(private[-1].content.decode())


async def test_a_problem_is_told_once(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: Kraken
) -> None:
    ivy = await _investor(as_system)
    await _connect(as_person, ivy)
    plan = await _plan(as_person, ivy)
    kraken.down = True
    await run_job("dca.cycle")
    await run_job("dca.cycle")
    (found,) = await _read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    async with as_system() as client:
        told = (
            await client.list(
                "sys.notification",
                where={
                    "and": [
                        {"field": "person", "op": "eq", "value": ivy},
                        {"field": "kind", "op": "eq", "value": "dca.problem"},
                    ]
                },
            )
        )["records"]
    assert found["last_message"] == "Problem: EService:Unavailable"
    assert len(told) == 1


async def test_plans_are_their_owners_alone(
    as_system: Acting, as_person: Acting, kraken: Kraken
) -> None:
    ivy, max_ = await _investor(as_system), await _investor(as_system)
    plan = await _plan(as_person, ivy)
    assert await _read(as_person, max_, "dca.plan", sys_id=plan["sys_id"]) == []
    async with as_person(max_) as client:
        with pytest.raises(Problem):
            await client.create(
                "dca.tier", {"plan": plan["sys_id"], "threshold_percent": "-5", "amount": "0.002"}
            )
    async with as_person(ivy) as client:
        with pytest.raises(Problem) as orders:
            await client.create(
                "dca.order",
                {
                    "plan": plan["sys_id"],
                    "pair": "XBTCHF",
                    "strategy": "manual",
                    "state": "filled",
                    "amount": "1",
                },
            )
        with pytest.raises(Problem) as bookkeeping:
            current = await client.get("dca.plan", plan["sys_id"])
            await client.update("dca.plan", plan["sys_id"], {"next_buy": None}, current["_etag"])
    assert orders.value.status == bookkeeping.value.status == 403


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ({"deposit_day": 29}, "deposit_day"),
        ({"amount": "0"}, "amount"),
        ({"mode": "lump_sum"}, "end_date"),
        ({"max_price": "-1"}, "max_price"),
    ],
)
async def test_plans_are_checked_when_saved(
    as_system: Acting, as_person: Acting, values: dict[str, Any], field: str
) -> None:
    ivy = await _investor(as_system)
    with pytest.raises(Problem) as refused:
        await _plan(as_person, ivy, **values)
    assert [e.field for e in refused.value.errors] == [field]
