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
        self.bid, self.ask, self.last = "79990.0", "80010.0", "80000.0"
        self.close = "80500.0"  # yesterday's

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - an API
        path = request.url.path
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if path == "/0/public/Ticker":
            return self._ok(
                {
                    "XXBTZCHF": {
                        "c": [self.last, "0.1"],
                        "b": [self.bid, "1", "1.0"],
                        "a": [self.ask, "1", "1.0"],
                    }
                }
            )
        if path == "/0/public/OHLC":
            day = int(datetime.now(UTC).timestamp()) // 86400 * 86400
            return self._ok(
                {
                    "XXBTZCHF": [
                        [day - 86400, "0", "0", "0", self.close, "0", "0", 1],
                        [day, "0", "0", "0", self.last, "0", "0", 1],
                    ],
                    "last": day,
                }
            )
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
                "userref": form.get("userref"),
                "cl_ord_id": form.get("cl_ord_id"),
                "descr": {"pair": "XBTCHF", "type": "buy"},
                "closetm": datetime.now(UTC).timestamp(),
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
            found = {
                t: o
                for t, o in self.orders.items()
                if o["status"] == ("open" if key == "open" else "closed")
                and all(o.get(f) == form[f] for f in ("userref", "cl_ord_id") if f in form)
            }
            return self._ok({key: found})
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
    assert sending["state"] == "submitting" and sending["userref"] is None
    assert kraken.orders == {}  # not before the cycle's change is kept and the worker sends it
    await deliver_calls()
    (order,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert (order["state"], Decimal(order["price"]), Decimal(order["fee"])) == (
        "filled",
        Decimal("80100.0"),
        Decimal("0.21"),
    )
    assert order["kraken_txid"] in kraken.orders and len(kraken.orders) == 1
    # Kraken knows it by the order's own ID.
    assert kraken.orders[order["kraken_txid"]]["cl_ord_id"] == order["sys_id"]


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
    assert len([o for o in mine if o["cl_ord_id"] == order["sys_id"]]) == 1  # placed once


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


def _someone_else(kraken: LiveKraken, status: str) -> None:
    kraken.orders["OTHER-AAAAA-BBBBBB"] = {
        "userref": None,
        "cl_ord_id": None,
        "descr": {"pair": "XBTCHF", "type": "buy"},
        "status": status,
        "vol": "0.001",
        "vol_exec": "0.001" if status == "closed" else "0",
        "cost": "80",
        "fee": "0.2",
        "price": "80000",
        "opentm": datetime.now(UTC).timestamp() - 600,
        "closetm": datetime.now(UTC).timestamp() - 600,
    }


@pytest.mark.parametrize("status", ["open", "closed"])
async def test_another_system_on_the_pair_holds_the_plan_until_resumed(
    as_system: Acting,
    as_person: Acting,
    run_job: RunJob,
    kraken: LiveKraken,
    status: str,
) -> None:
    await _allow(as_system, True)
    ivy, plan = await _live_plan(as_system, as_person)
    _someone_else(kraken, status)
    await run_job("dca.cycle")
    (held,) = await read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert held["status"] == "hold" and "OTHER-AAAAA-BBBBBB" in held["hold_reason"]
    assert await read(as_person, ivy, "dca.order", plan=plan["sys_id"]) == []
    # Resumed: what was there before is known now; the plan buys again.
    async with as_person(ivy) as client:
        await client.action("dca.plan", plan["sys_id"], "dca.resume")
    async with as_system() as client:
        current = await client.get("dca.plan", plan["sys_id"])
        await client.update(
            "dca.plan",
            plan["sys_id"],
            {"next_buy": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            current["_etag"],
        )
    await run_job("dca.cycle")
    (sending,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert sending["state"] == "submitting"


async def test_a_sell_on_the_pair_is_no_reason_to_hold(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: LiveKraken
) -> None:
    await _allow(as_system, True)
    ivy, plan = await _live_plan(as_system, as_person)
    _someone_else(kraken, "open")
    kraken.orders["OTHER-AAAAA-BBBBBB"]["descr"]["type"] = "sell"
    await run_job("dca.cycle")
    (sending,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert sending["state"] == "submitting"


@pytest.mark.parametrize(
    ("bid", "ask", "last", "said"),
    [
        ("78000.0", "80000.0", "79000.0", "apart"),  # 2.6 % between selling and buying
        ("89990.0", "90010.0", "90000.0", "yesterday's close"),  # +11.8 %
    ],
)
async def test_a_strange_market_makes_the_buy_wait(  # noqa: PLR0913, PLR0917 - the market
    as_system: Acting,
    as_person: Acting,
    run_job: RunJob,
    kraken: LiveKraken,
    bid: str,
    ask: str,
    last: str,
    said: str,
) -> None:
    await _allow(as_system, True)
    ivy, plan = await _live_plan(as_system, as_person)
    kraken.bid, kraken.ask, kraken.last = bid, ask, last
    await run_job("dca.cycle")
    (waiting,) = await read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert waiting["status"] == "ok" and waiting["last_message"].startswith("Waiting:")
    assert said in waiting["last_message"]
    assert await read(as_person, ivy, "dca.order", plan=plan["sys_id"]) == []
    kraken.bid, kraken.ask, kraken.last = "79990.0", "80010.0", "80000.0"
    await run_job("dca.cycle")  # normal again: it buys
    (sending,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    assert sending["state"] == "submitting"


async def test_a_plan_trades_through_one_of_its_owners_own_connections(
    as_system: Acting, as_person: Acting, kraken: LiveKraken
) -> None:
    ivy = await investor(as_system)
    other = await investor(as_system)
    await connect(as_person, ivy)
    await connect(as_person, other)
    plan = await new_plan(as_person, ivy)
    async with as_person(ivy) as client:
        (mine,) = (await client.list("sys.connection"))["records"]
    assert plan["connection"] == mine["sys_id"]  # their only one, without saying
    async with as_person(other) as client:
        (theirs,) = (await client.list("sys.connection"))["records"]
    with pytest.raises(Problem) as not_hers:
        await new_plan(as_person, ivy, connection=theirs["sys_id"])
    assert [e.field for e in not_hers.value.errors] == ["connection"]
    # Real orders need the plan's own connection to have passed its check.
    async with as_person(ivy) as client:
        current = await client.get("dca.plan", plan["sys_id"])
        with pytest.raises(Problem) as unchecked:
            await client.update(
                "dca.plan", plan["sys_id"], {"keys_checked": True, "live": True}, current["_etag"]
            )
    assert [e.field for e in unchecked.value.errors] == ["live"]


async def test_a_plan_from_before_gets_its_connection_named_later(
    as_system: Acting, as_person: Acting, kraken: LiveKraken
) -> None:
    ivy = await investor(as_system)
    plan = await new_plan(as_person, ivy)  # no connection yet: none to name
    assert plan["connection"] is None
    await connect(as_person, ivy)
    async with as_person(ivy) as client:
        (mine,) = (await client.list("sys.connection"))["records"]
        current = await client.get("dca.plan", plan["sys_id"])
        named = await client.update(
            "dca.plan", plan["sys_id"], {"connection": mine["sys_id"]}, current["_etag"]
        )
    assert named["connection"] == mine["sys_id"]
