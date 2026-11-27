"""Pairs as records: read from Kraken (coins and tokenized stocks), chosen by plans, bought by
the cycle under Kraken's own name, and plans from before linked by the upgrade."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from p42cloud.sdk import Problem
from p42cloud.sdk.testing import RunJob, StandIn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from p42cloud_dca.kraken import TYPE, add_order, pairs
from p42cloud_dca.pairs import move_plans
from tests.helpers import Acting, connect, investor, new_plan, pair_id, read

# How Kraken lists pairs: a coin, one paid in pounds, one offline; and apart from them, only
# when asked for tokenized assets, a stock.
COINS: dict[str, Any] = {
    "XXBTZCHF": {"altname": "XBTCHF", "wsname": "XBT/CHF", "status": "online"},
    "XXBTZGBP": {"altname": "XBTGBP", "wsname": "XBT/GBP", "status": "online"},
    "ADAEUR": {"altname": "ADAEUR", "wsname": "ADA/EUR", "status": "delisted"},
}
STOCKS: dict[str, Any] = {
    "NVDAxUSD": {
        "altname": "NVDAxUSD",
        "wsname": "NVDAx/USD",
        "aclass_base": "tokenized_asset",
        "status": "online",
        "ordermin": "0.01",
        "lot_decimals": 6,
        "costmin": "1",
    },
}


def listed(params: httpx.QueryParams) -> Any:
    """AssetPairs as Kraken answers it: stocks only with their class, even by name."""
    stocks = params.get("aclass_base") == "tokenized_asset"
    found = STOCKS if stocks else COINS
    asked = params.get("pair")
    return {k: v for k, v in found.items() if not asked or v["altname"] == asked}


class Kraken:
    """Answers like Kraken for NVDAx/USD: its pairs, price, minimums and a dollar balance."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        result: Any
        if path == "/0/public/AssetPairs":
            result = listed(params)
        elif path in ("/0/public/Ticker", "/0/public/OHLC"):
            if params.get("asset_class") != "tokenized_asset":
                return httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"]})
            result = {"NVDAxUSD": {"c": ["180.50", "1"]}}
        elif path == "/0/private/BalanceEx":
            result = {"ZUSD": {"balance": "1000", "hold_trade": "0"}}
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={"error": [], "result": result})


@pytest.fixture
def kraken(stand_in: StandIn) -> Kraken:
    made = Kraken()
    stand_in(TYPE, made)
    return made


async def test_krakens_list_becomes_coins_and_stocks() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": [], "result": listed(request.url.params)})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(answer), base_url="https://api.kraken.com"
    ) as http:
        found = {p["code"]: p for p in await pairs(http)}
    assert set(found) == {"XBTCHF", "NVDAxUSD"}  # pounds and delisted ones aren't offered
    assert found["XBTCHF"] | {} == {
        "code": "XBTCHF",
        "label": "BTC / CHF",
        "asset": "BTC",
        "currency": "CHF",
        "kind": "coin",
    }
    assert (found["NVDAxUSD"]["label"], found["NVDAxUSD"]["kind"]) == ("NVDAx / USD", "stock")


async def test_a_stock_order_names_its_asset_class() -> None:
    sent: list[bytes] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"error": [], "result": {"txid": ["O-1"]}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(answer), base_url="https://api.kraken.com"
    ) as http:
        await add_order(http, "NVDAxUSD", Decimal("0.2"), "a", stock=True)
        await add_order(http, "XBTCHF", Decimal("0.001"), "b")
    assert b"asset_class=tokenized_asset" in sent[0]
    assert b"asset_class" not in sent[1]  # Kraken refuses it for coins


async def test_a_plan_buys_a_tokenized_stock_by_krakens_name(
    as_system: Acting, as_person: Acting, run_job: RunJob, kraken: Kraken
) -> None:
    ivy = await investor(as_system)
    await connect(as_person, ivy)
    await new_plan(as_person, ivy)  # Kraken's pairs are read through a plan's connection
    state, said = await run_job("dca.pairs")
    assert state == "succeeded" and said is not None and "1 new" in said
    async with as_person(ivy) as client:
        stocks = (
            await client.list("dca.pair", where={"field": "kind", "op": "eq", "value": "stock"})
        )["records"]
        gone = await client.get("dca.pair", await pair_id(client, "ETHEUR"))
    assert [s["code"] for s in stocks] == ["NVDAxUSD"]
    assert not gone["offered"]  # not in Kraken's list now

    plan = await new_plan(as_person, ivy, code="NVDAxUSD", name="Nvidia", amount="50")
    assert plan["amount_in"] == "currency"  # a stock is bought for money: 50 USD a time
    await run_job("dca.cycle")
    (planned,) = await read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    async with as_system() as client:  # its time has come
        await client.update(
            "dca.plan",
            plan["sys_id"],
            {"next_buy": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
            planned["_etag"],
        )
    await run_job("dca.cycle")
    (order,) = await read(as_person, ivy, "dca.order", plan=plan["sys_id"])
    (after,) = await read(as_person, ivy, "dca.plan", sys_id=plan["sys_id"])
    assert (order["pair"], order["state"]) == ("NVDAxUSD", "dry_run")
    # 50 USD at 180.50, down to Kraken's 6 decimals for the stock.
    assert Decimal(order["amount"]) == Decimal("0.277008")
    assert Decimal(order["cost"]) <= 50
    assert after["last_message"].startswith("Dry run: would have bought")
    assert "NVDAx" in after["last_message"] and "USD" in after["last_message"]


async def test_a_plan_needs_a_pair_kraken_offers(
    as_system: Acting, as_person: Acting, kraken: Kraken
) -> None:
    ivy = await investor(as_system)
    async with as_person(ivy) as client:
        with pytest.raises(Problem) as none:
            await client.create("dca.plan", {"name": "Nothing", "amount": "1"})
    async with as_system() as client:
        eth = await client.get("dca.pair", await pair_id(client, "ETHCHF"))
        await client.update("dca.pair", eth["sys_id"], {"offered": False}, eth["_etag"])
    try:
        with pytest.raises(Problem) as gone:
            await new_plan(as_person, ivy, code="ETHCHF")
    finally:
        async with as_system() as client:
            eth = await client.get("dca.pair", eth["sys_id"])
            await client.update("dca.pair", eth["sys_id"], {"offered": True}, eth["_etag"])
    assert [e.field for e in none.value.errors] == ["trading_pair"]
    assert "no longer offers ETH / CHF" in gone.value.errors[0].message


async def test_plans_from_before_are_linked_to_their_pair(
    as_system: Acting, as_person: Acting, p42_installation: Any
) -> None:
    """A stand-in for 0.8's column (its choice stays there, retired)."""
    ivy = await investor(as_system)
    plan = await new_plan(as_person, ivy, code="XBTCHF")
    owner = create_async_engine(p42_installation.owner_database_url)  # as an upgrade runs
    try:
        async with owner.begin() as conn:
            await conn.execute(text("ALTER TABLE dca.plan ADD COLUMN pair text"))
            await conn.execute(
                text("UPDATE dca.plan SET pair = 'xbtchf', trading_pair = NULL WHERE sys_id = :i"),
                {"i": plan["sys_id"]},
            )
            await move_plans(conn)
            linked = await conn.scalar(
                text(
                    "SELECT r.code FROM dca.plan p JOIN dca.pair r ON r.sys_id = p.trading_pair"
                    " WHERE p.sys_id = :i"
                ),
                {"i": plan["sys_id"]},
            )
            await conn.execute(text("ALTER TABLE dca.plan DROP COLUMN pair"))
    finally:
        await owner.dispose()
    assert linked == "XBTCHF"
