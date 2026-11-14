"""Talking to Kraken: the connection type, signed private calls, and what the cycle reads.

Private calls carry a nonce that must keep growing for each key; it's taken from the clock in
microseconds. Kraken's errors come back in the answer's `error` list; some only mean "not now".
"""

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
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


def _of_pair(result: dict[str, Any], pair: str) -> Any:
    """Kraken names pairs its own way (XXBTZCHF for XBTCHF): matched ignoring X and Z."""
    if pair in result:
        return result[pair]
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


async def price(http: httpx.AsyncClient, pair: str) -> Decimal:
    """The last trade's price."""
    ticker = _of_pair(await public(http, "Ticker", pair=pair), pair)
    return Decimal(str(ticker["c"][0]))


async def market(http: httpx.AsyncClient, pair: str) -> Market:
    info = _of_pair(await public(http, "AssetPairs", pair=pair), pair)
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
    """A plan's pair as Kraken names it, and its two currencies."""

    name: str  # XBTCHF

    @property
    def asset(self) -> str:
        return "BTC" if self.name.startswith("XBT") else self.name[:3]

    @property
    def currency(self) -> str:
        return self.name[3:]
