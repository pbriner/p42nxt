"""Talking to Kraken: the connection type, signed private calls, and what the cycle reads.

Private calls carry a nonce that must keep growing for each key; it's taken from the clock in
microseconds. Kraken's errors come back in the answer's `error` list; some only mean "not now".
"""

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from urllib.parse import parse_qs

import httpx
from p42cloud.sdk import ConnectionType

from p42cloud_dca.strategy import Market

TYPE = "dca.kraken"
# What Kraken means as "try again later"; everything else is its final word.
TEMPORARY = ("EService:", "EAPI:Rate limit", "EGeneral:Temporary lockout", "EAPI:Invalid nonce")


class KrakenError(Exception):
    """Kraken answered with errors; `temporary` when trying again later may work."""

    @property
    def temporary(self) -> bool:
        return str(self).startswith(TEMPORARY)


def sign(request: httpx.Request, secrets: dict[str, str]) -> None:
    """Kraken's signature: HMAC-SHA512 with the decoded secret over the path and SHA-256 of the
    nonce followed by the body."""
    if not request.url.path.startswith("/0/private/"):
        return
    body = request.content.decode()
    nonce = parse_qs(body).get("nonce", [""])[0]
    digest = hashlib.sha256((nonce + body).encode()).digest()
    try:
        key = base64.b64decode(secrets.get("api_secret", ""), validate=True)
    except ValueError:
        key = b""  # Kraken refuses the signature, which says what's wrong
    mac = hmac.new(key, request.url.path.encode() + digest, hashlib.sha512)
    request.headers["API-Key"] = secrets.get("api_key", "")
    request.headers["API-Sign"] = base64.b64encode(mac.digest()).decode()


def _result(answer: httpx.Response) -> Any:
    answer.raise_for_status()
    body = cast(dict[str, Any], answer.json())
    errors = cast(list[str], body.get("error") or [])
    if errors:
        raise KrakenError(", ".join(errors))
    return body.get("result")


async def private(http: httpx.AsyncClient, path: str, **form: str) -> Any:
    nonce = str(time.time_ns() // 1000)
    return _result(await http.post(f"/0/private/{path}", data={"nonce": nonce, **form}))


async def public(http: httpx.AsyncClient, path: str, **params: str) -> Any:
    return _result(await http.get(f"/0/public/{path}", params=params))


TOKENIZED = "tokenized_asset"  # Kraken's asset class for xStocks, asked for on each call


def _of(pair: Pair) -> dict[str, str]:
    """A pair as Kraken's market calls take it: a stock also names its asset class, or Kraken
    answers that the pair is unknown (and refuses the class for coins)."""
    return {"pair": pair.name, "asset_class": TOKENIZED} if pair.stock else {"pair": pair.name}


def _altname(value: object) -> object:
    """A pair's own name in an answer (price history answers lists, not pairs)."""
    return cast(dict[str, Any], value).get("altname") if isinstance(value, dict) else None


def _of_pair(result: dict[str, Any], pair: str) -> Any:
    """Kraken names pairs its own way (XXBTZCHF for XBTCHF): by its exact name first (a
    tokenized stock like NVDAxUSD is answered by that name), else ignoring X and Z."""
    if pair in result:
        return result[pair]
    for name, found in result.items():
        named = _altname(found)
        if name.lower() == pair.lower() or named == pair:
            return found
    bare = pair.replace("X", "").replace("Z", "")
    for name, value in result.items():
        if name.replace("X", "").replace("Z", "") == bare:
            return value
    raise KrakenError(f"Pair {pair} not found")


async def check(http: httpx.AsyncClient) -> str:
    await private(http, "BalanceEx")
    return "Kraken accepts the keys."


KRAKEN = ConnectionType(
    "kraken",
    "Kraken",
    allowed_hosts=("api.kraken.com",),
    auth="signed",
    secrets=("api_key", "api_secret"),
    base_url="https://api.kraken.com",
    sign=sign,
    check=check,
)


async def price(http: httpx.AsyncClient, pair: Pair) -> Decimal:
    """The last trade's price."""
    ticker = _of_pair(await public(http, "Ticker", **_of(pair)), pair.name)
    return Decimal(str(ticker["c"][0]))


@dataclass(frozen=True)
class Ticker:
    """The last trade's price and the best prices to sell (bid) and buy (ask) at now."""

    last: Decimal
    bid: Decimal
    ask: Decimal

    @property
    def gap(self) -> Decimal:
        """The gap between buying and selling, as a share of the selling price."""
        return (self.ask - self.bid) / self.bid if self.bid > 0 else Decimal(1)


async def ticker(http: httpx.AsyncClient, pair: Pair) -> Ticker:
    found = _of_pair(await public(http, "Ticker", **_of(pair)), pair.name)
    return Ticker(
        last=Decimal(str(found["c"][0])),
        bid=Decimal(str(found["b"][0])),
        ask=Decimal(str(found["a"][0])),
    )


async def market(http: httpx.AsyncClient, pair: Pair) -> Market:
    # Pair details name the class differently from the other calls.
    asked = {"pair": pair.name, "aclass_base": TOKENIZED} if pair.stock else {"pair": pair.name}
    info = _of_pair(await public(http, "AssetPairs", **asked), pair.name)
    return Market(
        ordermin=Decimal(str(info.get("ordermin") or "0")),
        lot_decimals=int(info.get("lot_decimals", 8)),
        costmin=Decimal(str(info.get("costmin") or "0")),
    )


async def free_balance(http: httpx.AsyncClient, currency: str) -> Decimal:
    """What's free in a currency: the balance less what open orders hold."""
    result = cast(dict[str, Any], await private(http, "BalanceEx"))
    for name in (f"Z{currency}", currency, f"X{currency}"):
        if name in result:
            entry = result[name]
            return Decimal(str(entry.get("balance", "0"))) - Decimal(
                str(entry.get("hold_trade", "0"))
            )
    return Decimal(0)


@dataclass(frozen=True)
class Pair:
    """A plan's pair: Kraken's name for it, what it buys and the currency it pays with."""

    name: str  # as Kraken names it: XBTCHF, NVDAxUSD
    asset: str = ""  # BTC, NVDAx
    currency: str = ""  # CHF
    stock: bool = False  # a tokenized stock: Kraken wants its asset class on every call

    def __post_init__(self) -> None:
        # Orders from before pairs were records only know the name (the six coin pairs).
        if not self.asset:
            object.__setattr__(
                self, "asset", "BTC" if self.name.startswith("XBT") else self.name[:3]
            )
        if not self.currency:
            object.__setattr__(self, "currency", self.name[-3:])


# What a pair's base is called where people read it (Kraken's own names for some coins).
_SHOWN = {"XBT": "BTC", "XXBT": "BTC", "XDG": "DOGE", "XXDG": "DOGE", "XETH": "ETH"}
CURRENCIES = ("CHF", "EUR", "USD")  # pairs paid in these are offered


def _currency(quote: str) -> str:
    return quote[1:] if len(quote) == 4 and quote[0] == "Z" else quote  # noqa: PLR2004 - ZCHF


def stock(base: str, info: dict[str, Any]) -> bool:
    """A tokenized stock (xStocks: NVDAx, AAPLx) rather than a coin."""
    if str(info.get("aclass_base", "")).startswith("tokenized"):
        return True
    return len(base) > 1 and base.endswith("x") and base[:-1].isupper()


async def pairs(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Every pair Kraken trades now, paid in CHF, EUR or USD, as dca.pair records. Kraken
    lists tokenized stocks only when asked for them, apart from the coins."""
    listed = cast(dict[str, Any], await public(http, "AssetPairs"))
    listed |= cast(dict[str, Any], await public(http, "AssetPairs", aclass_base=TOKENIZED))
    found: list[dict[str, Any]] = []
    for info in listed.values():
        wsname = str(info.get("wsname") or "")
        if "/" not in wsname or str(info.get("status", "online")) != "online":
            continue
        base, quote = wsname.split("/", 1)
        if _currency(quote) not in CURRENCIES:
            continue
        asset = _SHOWN.get(base, base)
        found.append(
            {
                "code": str(info["altname"]),
                "label": f"{asset} / {_currency(quote)}",
                "asset": asset,
                "currency": _currency(quote),
                "kind": "stock" if stock(base, info) else "coin",
            }
        )
    return found


@dataclass(frozen=True)
class Fill:
    """What Kraken says about an order."""

    txid: str
    status: str  # pending, open, closed, canceled, expired
    volume: Decimal  # executed
    cost: Decimal
    fee: Decimal
    price: Decimal

    def answer(self) -> dict[str, str]:
        return {
            "txid": self.txid,
            "status": self.status,
            "volume": str(self.volume),
            "cost": str(self.cost),
            "fee": str(self.fee),
            "price": str(self.price),
        }


def _fill(txid: str, info: dict[str, Any]) -> Fill:
    return Fill(
        txid=txid,
        status=str(info.get("status", "")),
        volume=Decimal(str(info.get("vol_exec") or "0")),
        cost=Decimal(str(info.get("cost") or "0")),
        fee=Decimal(str(info.get("fee") or "0")),
        price=Decimal(str(info.get("price") or "0")),
    )


def _ref(reference: int | str) -> dict[str, str]:
    """Our reference as Kraken takes it: the order's ID (`cl_ord_id`, a UUID), or the number
    orders sent before 0.6.0 carry (`userref`)."""
    return {"userref": str(reference)} if isinstance(reference, int) else {"cl_ord_id": reference}


async def add_order(
    http: httpx.AsyncClient,
    pair: str,
    volume: Decimal,
    reference: int | str,
    *,
    stock: bool = False,
) -> str:
    """A market buy, carrying our reference so it can be found again; Kraken's order ID."""
    result = await private(
        http,
        "AddOrder",
        **({"asset_class": TOKENIZED} if stock else {}),
        pair=pair,
        type="buy",
        ordertype="market",
        volume=format(volume, "f"),
        **_ref(reference),
    )
    answer = cast(dict[str, Any], result or {})
    txids = cast(list[str], answer.get("txid") or [])
    if not txids:
        raise KrakenError("EGeneral:No order ID in Kraken's answer")
    return txids[0]


async def order(http: httpx.AsyncClient, txid: str) -> Fill | None:
    result = cast(dict[str, Any], await private(http, "QueryOrders", txid=txid) or {})
    info: Any = result.get(txid)
    return _fill(txid, cast(dict[str, Any], info)) if isinstance(info, dict) else None


async def find(http: httpx.AsyncClient, reference: int | str) -> Fill | None:
    """An order placed with our reference, open or closed; None if Kraken has none."""
    for path, key in (("OpenOrders", "open"), ("ClosedOrders", "closed")):
        result = cast(dict[str, Any], await private(http, path, **_ref(reference)) or {})
        for txid, info in cast(dict[str, Any], result.get(key) or {}).items():
            return _fill(txid, cast(dict[str, Any], info))
    return None


async def closes(http: httpx.AsyncClient, pair: Pair) -> list[tuple[datetime, Decimal]]:
    """Each day's close (UTC days), as far back as Kraken gives them (720 days)."""
    answer = await public(http, "OHLC", **_of(pair), interval="1440")
    candles = cast(list[list[Any]], _of_pair(answer, pair.name))
    return [(datetime.fromtimestamp(int(c[0]), tz=UTC), Decimal(str(c[4]))) for c in candles]


async def last_close(http: httpx.AsyncClient, pair: Pair) -> Decimal | None:
    """Yesterday's close: the last finished day (Kraken's last candle is today, still open)."""
    days = await closes(http, pair)
    return days[-2][1] if len(days) >= 2 else None  # noqa: PLR2004 - today and the day before


def _same_pair(described: str, pair: str) -> bool:
    if described.lower() == pair.lower():
        return True
    return described.replace("X", "").replace("Z", "") == pair.replace("X", "").replace("Z", "")


@dataclass(frozen=True)
class Other:
    """An order on the pair this plan didn't place."""

    txid: str
    status: str
    kind: str  # buy, sell
    volume: Decimal
    at: datetime


async def others(
    http: httpx.AsyncClient, pair: str, since: datetime, ours: set[str]
) -> list[Other]:
    """Buys on the pair since then that aren't ours (by Kraken's order ID or our reference),
    open or done: another system, or someone by hand, buying the same pair. Sells don't spend
    the plan's money; an open buy older than `since` is left alone until it fills."""
    found: list[Other] = []
    opened = cast(dict[str, Any], await private(http, "OpenOrders") or {})
    closed = cast(
        dict[str, Any],
        await private(http, "ClosedOrders", start=str(int(since.timestamp()))) or {},
    )
    for key, result in (("open", opened), ("closed", closed)):
        for txid, raw in cast(dict[str, Any], result.get(key) or {}).items():
            info = cast(dict[str, Any], raw)
            descr = cast(dict[str, Any], info.get("descr") or {})
            mine = txid in ours or str(info.get("cl_ord_id") or "") in ours
            if mine or descr.get("type") != "buy":
                continue
            if not _same_pair(str(descr.get("pair", "")), pair):
                continue
            volume = Decimal(str(info.get("vol_exec") or "0"))
            if key == "closed" and volume <= 0:
                continue  # cancelled before anything was bought
            stamp = info.get("closetm" if key == "closed" else "opentm") or 0
            at = datetime.fromtimestamp(float(stamp), tz=UTC)
            if at < since:
                continue  # looked at already (Resume), or before the cooldown
            found.append(
                Other(
                    txid,
                    str(info.get("status", key)),
                    str(descr.get("type", "")),
                    volume if key == "closed" else Decimal(str(info.get("vol") or "0")),
                    at,
                )
            )
    return found
