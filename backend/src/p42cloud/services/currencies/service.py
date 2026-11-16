"""Converting amounts with the rates of a day, the built-in currencies, and the ECB's rates.

A conversion uses the latest rate on or before the day, at most WINDOW days old: a rate between
the two currencies, either way round, or through a currency both have a rate to (the ECB's rates
are all in EUR). No rate found: refused, never guessed.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.types.currency import TYPE as CURRENCY
from p42cloud.data.types.decimal import number
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.outgoing import safe_client

WINDOW = timedelta(days=10)
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_HOST = "www.ecb.europa.eu"
# Tests answer for the ECB with their own transport (still guarded by the safe client).
TEST_TRANSPORT: list[httpx.AsyncBaseTransport] = []
_DAY = re.compile(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]")
_RATE = re.compile(r"<Cube\s+currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9.]+)['\"]")

# The currencies every installation starts with: code, English name, decimals.
BUILT_IN = (
    ("CHF", "Swiss franc", 2),
    ("EUR", "Euro", 2),
    ("USD", "US dollar", 2),
    ("GBP", "Pound sterling", 2),
    ("JPY", "Japanese yen", 0),
    ("CAD", "Canadian dollar", 2),
    ("AUD", "Australian dollar", 2),
    ("SEK", "Swedish krona", 2),
    ("NOK", "Norwegian krone", 2),
    ("DKK", "Danish krone", 2),
    ("PLN", "Polish złoty", 2),
    ("CZK", "Czech koruna", 2),
    ("CNY", "Chinese yuan", 2),
    ("BTC", "Bitcoin", 8),
    ("ETH", "Ether", 8),
)


async def seed(conn: AsyncConnection) -> None:
    """The built-in currencies, once; administrators' changes to them stay."""
    for code, name, decimals in BUILT_IN:
        await conn.execute(
            text(
                "INSERT INTO sys.currency (name, label, decimals, active, custom)"
                " SELECT CAST(:code AS varchar), CAST(:label AS jsonb), :decimals, true, false"
                " WHERE NOT EXISTS (SELECT 1 FROM sys.currency WHERE name = CAST(:code AS varchar))"
            ),
            {"code": code, "label": json.dumps({"en": name}), "decimals": decimals},
        )


async def _rates(conn: AsyncConnection, codes: set[str], on: date) -> dict[str, dict[str, Decimal]]:
    """For each currency, what one unit is worth in each other currency it has a rate to (the
    latest within the window), both ways round."""
    await switch_role(conn, "sys")
    rows = await conn.execute(
        text(
            "SELECT DISTINCT ON (currency, base) currency, base, rate FROM sys.exchange_rate"
            " WHERE deleted IS NULL AND date <= :on AND date >= :since"
            " AND (currency = ANY(:codes) OR base = ANY(:codes))"
            " ORDER BY currency, base, date DESC"
        ),
        {"on": on, "since": on - WINDOW, "codes": sorted(codes)},
    )
    worth: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        rate = Decimal(row.rate)
        if rate <= 0:
            continue
        worth.setdefault(row.currency, {}).setdefault(row.base, rate)
        worth.setdefault(row.base, {}).setdefault(row.currency, 1 / rate)
    return worth


async def rate(conn: AsyncConnection, source: str, target: str, on: date) -> Decimal:
    """What one unit of `source` is worth in `target` on a day."""
    source, target = CURRENCY.check(source, {}), CURRENCY.check(target, {})
    if source == target:
        return Decimal(1)
    worth = await _rates(conn, {source, target}, on)
    direct = worth.get(source, {}).get(target)
    if direct is not None:
        return direct
    for middle, in_middle in sorted(worth.get(source, {}).items()):
        back = worth.get(target, {}).get(middle)
        if back is not None:
            return in_middle / back
    raise Problem(
        404,
        "no_rate",
        f"There's no exchange rate from {source} to {target} on {on.isoformat()}"
        f" or in the {WINDOW.days} days before.",
    )


async def convert(
    conn: AsyncConnection, amount: Any, source: str, target: str, on: date | None = None
) -> Decimal:
    """The amount in another currency, with the rate of the day (today if not given)."""
    day = on or date.today()
    return number(amount) * await rate(conn, source, target, day)


# ---- the ECB's daily rates ----


def ecb_rates(body: str) -> tuple[date, dict[str, Decimal]]:
    """The day and the rates (one EUR in each currency) from the ECB's daily file."""
    day = _DAY.search(body)
    if day is None:
        raise Problem(502, "ecb", "The ECB's file names no day.")
    found = {code: Decimal(value) for code, value in _RATE.findall(body)}
    if not found:
        raise Problem(502, "ecb", "The ECB's file holds no rates.")
    return date.fromisoformat(day.group(1)), found


async def load_ecb(conn: AsyncConnection) -> str:
    """Reads the ECB's rates of the day and records them (one unit of each currency in EUR)."""
    transport = TEST_TRANSPORT[0] if TEST_TRANSPORT else None
    async with safe_client(allowed_hosts=(ECB_HOST,), transport=transport) as http:
        answer = await http.get(ECB_URL)
    if not answer.is_success:
        raise Problem(502, "ecb", f"The ECB answered {answer.status_code}.")
    day, rates = ecb_rates(answer.text)
    await switch_role(conn, "sys")
    added = 0
    for code, per_euro in sorted(rates.items()):
        if per_euro <= 0:
            continue
        made = await conn.execute(
            text(
                "INSERT INTO sys.exchange_rate (date, currency, base, rate, origin)"
                " SELECT CAST(:day AS date), CAST(:code AS varchar), 'EUR', :rate, 'ECB'"
                " WHERE NOT EXISTS (SELECT 1 FROM sys.exchange_rate"
                " WHERE date = CAST(:day AS date) AND currency = CAST(:code AS varchar)"
                " AND base = 'EUR' AND deleted IS NULL)"
            ),
            {"day": day, "code": code, "rate": round(1 / per_euro, 12)},
        )
        added += made.rowcount
    return f"{added} rates of {day.isoformat()} recorded from the ECB."
