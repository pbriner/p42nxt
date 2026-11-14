"""Real orders against a stand-in Kraken: sent once, filled with Kraken's figures, refusals and
unclear orders hold the plan, and nothing is sent unless every switch says so."""

import itertools
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from p42cloud.sdk import Problem
from p42cloud.sdk.testing import DeliverCalls, RunJob, StandIn

from p42cloud_dca.kraken import TYPE
from tests.helpers import Acting, connect, investor, new_plan, read

TXIDS = itertools.count(1)


class LiveKraken:
    """A stand-in Kraken that takes orders."""

    def __init__(self) -> None:
        self.mode = "ok"  # ok, refuse, lose (placed, but the answer is lost)
        self.orders: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - an API
        path = request.url.path
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if path == "/0/public/Ticker":
            return self._ok({"XXBTZCHF": {"c": ["80000.0", "0.1"]}})
        if path == "/0/public/AssetPairs":
            return self._ok(
                {"XXBTZCHF": {"ordermin": "0.00005", "lot_decimals": 8, "costmin": "0.5"}}
            )
        if path == "/0/private/BalanceEx":
            return self._ok({"ZCHF": {"balance": "1000", "hold_trade": "0"}})
        if path == "/0/private/AddOrder":
            if self.mode == "refuse":
                return httpx.Response(200, json={"error": ["EOrder:Insufficient funds"]})
            txid = f"O{next(TXIDS):05d}-AAAAA-BBBBBB"
            volume = form["volume"]
            self.orders[txid] = {
                "userref": int(form["userref"]),
                "status": "closed",
                "vol_exec": volume,
                "cost": "80.10",
                "fee": "0.21",
                "price": "80100.0",
            }
            if self.mode == "lose":
                raise httpx.ReadTimeout("no answer")
            return self._ok({"txid": [txid]})
        if path == "/0/private/QueryOrders":
            return self._ok({t: o for t, o in self.orders.items() if t == form["txid"]})
        if path in ("/0/private/OpenOrders", "/0/private/ClosedOrders"):
            key = "open" if "Open" in path else "closed"
            mine = {t: o for t, o in self.orders.items() if str(o["userref"]) == form["userref"]}
            return self._ok({key: mine if key == "closed" else {}})
        return httpx.Response(404)

    @staticmethod
    def _ok(result: Any) -> httpx.Response:
        return httpx.Response(200, json={"error": [], "result": result})


@pytest.fixture
def kraken(stand_in: StandIn) -> LiveKraken:
    made = LiveKraken()
    stand_in(TYPE, made)
    return made


async def _allow(as_system: Acting, allowed: bool) -> None:
    async with as_system() as client:
        found = await client.list(
            "sys.setting", where={"field": "name", "op": "eq", "value": "dca.live_allowed"}
        )
        setting = found["records"][0]
        await client.update("sys.setting", setting["sys_id"], {"value": allowed}, setting["_etag"])


async def _live_plan(as_system: Acting, as_person: Acting) -> tuple[str, dict[str, Any]]:
    """An investor whose checked connection lets a plan go live; the plan is due now."""
    ivy = await investor(as_system)
    await connect(as_person, ivy)
    async with as_person(ivy) as client:
        (connection,) = (await client.list("sys.connection"))["records"]
        await client.action("sys.connection", connection["sys_id"], "sys.check")
    plan = await new_plan(as_person, ivy, live=True, keys_checked=True)
    async with as_system() as client:
        current = await client.get("dca.plan", plan["sys_id"])
        await client.update(
            "dca.plan",
            plan["sys_id"],
            {"next_buy": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            current["_etag"],
        )
    return ivy, plan


async def test_going_live_needs_the_key_checked_and_a_working_connection(
    as_system: Acting, as_person: Acting, kraken: LiveKraken
) -> None:
    ivy = await investor(as_system)
    with pytest.raises(Problem) as unchecked:
        await new_plan(as_person, ivy, live=True)
    with pytest.raises(Problem) as no_connection:
        await new_plan(as_person, ivy, live=True, keys_checked=True)
    assert [e.field for e in unchecked.value.errors] == ["keys_checked"]
    assert [e.field for e in no_connection.value.errors] == ["live"]


async def test_nothing_is_sent_while_the_installation_says_no(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: LiveKraken
) -> None:
    await _allow(as_system, False)
    ivy, plan = await _live_plan(as_system, as_person)
    await run_job("dca.cycle")
    (found,) = await read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert found["last_message"].startswith("Real orders are switched off")
    assert await read(as_person, ivy, "dca.order", plan=plan["sys_id"]) == []


async def test_a_real_order_is_sent_once_and_filled_with_krakens_figures(
    as_system: Acting,
    as_person: Acting,
    run_job: RunJob,
    deliver_calls: DeliverCalls,
    kraken: LiveKraken,
) -> None:
    await _allow(as_system, True)
    ivy, plan = await _live_plan(as_system, as_person)
    await run_job("dca.cycle")
    (sending,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert sending["state"] == "submitting" and sending["userref"]
    assert kraken.orders == {}  # not before the cycle's change is kept and the worker sends it
    await deliver_calls()
    (order,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert (order["state"], Decimal(order["price"]), Decimal(order["fee"])) == (
        "filled",
        Decimal("80100.0"),
        Decimal("0.21"),
    )
    assert order["kraken_txid"] in kraken.orders and len(kraken.orders) == 1


async def test_a_lost_answer_is_asked_about_and_not_sent_twice(
    as_system: Acting,
    as_person: Acting,
    run_job: RunJob,
    deliver_calls: DeliverCalls,
    kraken: LiveKraken,
) -> None:
    await _allow(as_system, True)
    ivy, plan = await _live_plan(as_system, as_person)
    await run_job("dca.cycle")
    kraken.mode = "lose"
    await deliver_calls()
    kraken.mode = "ok"
    await deliver_calls(later=True)
    mine = [o for o in kraken.orders.values()]
    (order,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert order["state"] == "filled"
    assert len([o for o in mine if o["userref"] == order["userref"]]) == 1  # placed once


async def test_a_refusal_holds_the_plan_until_its_owner_resumes_it(
    as_system: Acting,
    as_person: Acting,
    run_job: RunJob,
    deliver_calls: DeliverCalls,
    kraken: LiveKraken,
) -> None:
    await _allow(as_system, True)
    ivy, plan = await _live_plan(as_system, as_person)
    await run_job("dca.cycle")
    kraken.mode = "refuse"
    await deliver_calls()
    (order,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    (held,) = await read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert order["state"] == "failed" and "Insufficient funds" in order["error"]
    assert held["status"] == "hold" and "Kraken refused" in held["hold_reason"]
    await run_job("dca.cycle")  # stays on hold: no new order
    assert len(await read(as_person, ivy, "dca.order", plan=plan["sys_id"])) == 1
    async with as_person(ivy) as client:
        with pytest.raises(Problem):
            current = await client.get("dca.plan", plan["sys_id"])
            await client.update("dca.plan", plan["sys_id"], {"hold_reason": "x"}, current["_etag"])
    async with as_person(ivy) as client:
        await client.action("dca.plan", plan["sys_id"], "dca.resume")
        resumed = await client.get("dca.plan", plan["sys_id"])
    assert (resumed["status"], resumed["hold_reason"]) == ("ok", None)
