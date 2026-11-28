"""The pairs a plan can buy: Kraken's own list, coins and tokenized stocks (NVDAx), paid in CHF,
EUR or USD. Read from Kraken daily (and by hand); the six coin pairs are there from the start."""

from typing import Any

import httpx
from p42cloud.sdk import Boolean, Choice, Client, JobRun, Problem, String, Table
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud_dca import kraken
from p42cloud_dca.kraken import TYPE as KRAKEN_TYPE
from p42cloud_dca.kraken import KrakenError, Pair
from p42cloud_dca.paging import every

PAIR = Table(
    "pair",
    "Pair",
    {
        "code": String("Kraken's name", max_length=40, required=True, unique=True),
        "label": String("Pair", max_length=80, required=True),
        "asset": String("Buys", max_length=40, required=True),
        "currency": String("Pays with", max_length=8, required=True),
        "kind": Choice(
            "Kind",
            {"coin": "Coin", "stock": "Tokenized stock"},
            required=True,
            default="coin",
        ),
        "offered": Boolean("Kraken offers it", required=True, default=True),
    },
    label_plural="Pairs",
    display="label",
)

# There before Kraken was ever asked, and what plans had before pairs were records.
COINS = (
    ("XBTCHF", "BTC", "CHF"),
    ("XBTEUR", "BTC", "EUR"),
    ("XBTUSD", "BTC", "USD"),
    ("ETHCHF", "ETH", "CHF"),
    ("ETHEUR", "ETH", "EUR"),
    ("ETHUSD", "ETH", "USD"),
)


async def seed(conn: AsyncConnection, _: Any) -> None:
    for code, asset, currency in COINS:
        await conn.execute(
            text(
                "INSERT INTO dca.pair (code, label, asset, currency, kind, offered)"
                " VALUES (:c, :l, :a, :cur, 'coin', true)"
                " ON CONFLICT (code) WHERE deleted IS NULL DO NOTHING"
            ),
            {"c": code, "l": f"{asset} / {currency}", "a": asset, "cur": currency},
        )


async def move_plans(conn: AsyncConnection) -> None:
    """0.8 plans named their pair by a fixed choice (xbtchf); now they point to its record."""
    await seed(conn, None)
    await conn.execute(
        text(
            "UPDATE dca.plan p SET trading_pair = r.sys_id FROM dca.pair r"
            " WHERE lower(r.code) = lower(p.pair) AND p.trading_pair IS NULL"
        )
    )


def pair_of(record: dict[str, Any]) -> Pair:
    """A pair record as the cycle uses it."""
    return Pair(
        str(record["code"]),
        str(record["asset"]),
        str(record["currency"]),
        stock=record.get("kind") == "stock",
    )


async def plan_pair(client: Client, plan: dict[str, Any]) -> Pair:
    """The pair a plan buys."""
    if not plan.get("trading_pair"):
        raise Problem(409, "no_pair", "The plan has no pair: choose one.")
    return pair_of(await client.get("dca.pair", str(plan["trading_pair"])))


async def order_pair(client: Client, code: str) -> Pair:
    """An order's pair by Kraken's name (orders keep the name they were placed with)."""
    found = await client.list("dca.pair", where={"field": "code", "op": "eq", "value": code})
    return pair_of(found["records"][0]) if found["records"] else Pair(code)


async def _offered(client: Client) -> list[dict[str, Any]] | None:
    """Kraken's list through the first plan's connection that answers (any will do)."""
    tried: set[tuple[str, str]] = set()
    for plan in await every(client, "dca.plan"):
        who = (str(plan["owner"]), str(plan.get("connection") or ""))
        if who in tried:
            continue
        tried.add(who)
        try:
            async with client.connection(
                KRAKEN_TYPE, owner=who[0], connection=who[1] or None, acting=False
            ) as http:
                return await kraken.pairs(http)
        except Problem, KrakenError, httpx.HTTPError:
            continue  # no connection, or it doesn't work now: the next one
    return None


async def refresh(run: JobRun) -> str:
    """Kraken's pairs now: new ones added, gone ones marked as no longer offered. Asked through
    a plan's Kraken connection (the list itself is public)."""
    client = Client(run.records)
    offered = await _offered(client)
    if offered is None:
        return "No plan with a working Kraken connection yet: pairs are read once there is one."
    known = {str(r["code"]): r for r in await every(client, "dca.pair")}
    added = 0
    for values in offered:
        record = known.pop(values["code"], None)
        if record is None:
            await client.create("dca.pair", values | {"offered": True})
            added += 1
        elif any(str(record.get(k)) != str(v) for k, v in values.items()) or not record["offered"]:
            await client.update(
                "dca.pair", record["sys_id"], values | {"offered": True}, record["_etag"]
            )
    gone = 0
    for record in known.values():
        if record["offered"]:
            await client.update("dca.pair", record["sys_id"], {"offered": False}, record["_etag"])
            gone += 1
    return f"Kraken offers {len(offered)} pairs: {added} new, {gone} no longer offered."
