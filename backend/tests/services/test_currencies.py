"""Amounts with a currency: the field types, the currencies, rates per day, conversion, and the
ECB's daily rates."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal
from importlib.metadata import version
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table, check
from p42cloud.data.types import FIELD_TYPES, Field, Invalid
from p42cloud.data.types.currency import Currency
from p42cloud.data.types.decimal import Decimal as DecimalField
from p42cloud.data.types.money import Money
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.services.currencies import service
from p42cloud.services.jobs.service import run_job
from tests.security.helpers import acting

DAY = date(2026, 11, 13)
ECB = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01">
 <Cube><Cube time='2026-11-13'>
  <Cube currency='USD' rate='1.0870'/>
  <Cube currency='CHF' rate='0.9350'/>
  <Cube currency='JPY' rate='163.40'/>
 </Cube></Cube>
</gesmes:Envelope>"""


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def _rates(engine: AsyncEngine, day: date, rates: dict[str, str], base: str) -> None:
    async with acting(engine, None) as records:
        for code, rate in rates.items():
            await records.create(
                "sys.exchange_rate",
                {"date": day.isoformat(), "currency": code, "base": base, "rate": rate},
            )


async def _convert(engine: AsyncEngine, amount: str, source: str, target: str, on: date) -> Decimal:
    async with acting(engine, None) as records:
        return await service.convert(records.conn, amount, source, target, on)


async def test_the_common_currencies_are_there(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        page = await records.list("sys.currency", limit=100)
    found = {r.values["name"]: r.values["decimals"] for r in page.records}
    assert found["CHF"] == 2 and found["JPY"] == 0 and found["BTC"] == 8


async def test_amounts_are_converted_with_the_rate_of_the_day(engine: AsyncEngine) -> None:
    day = DAY - timedelta(days=400)  # a day of its own, apart from other tests' rates
    await _rates(engine, day, {"CHF": "1.07", "USD": "0.92"}, "EUR")
    assert await _convert(engine, "100", "CHF", "EUR", day) == Decimal("107.00")
    assert await _convert(engine, "107", "EUR", "CHF", day) == pytest.approx(Decimal(100))
    via_euro = await _convert(engine, "100", "CHF", "USD", day + timedelta(days=3))
    assert via_euro == pytest.approx(Decimal("100") * Decimal("1.07") / Decimal("0.92"))
    assert await _convert(engine, "12.5", "CHF", "CHF", day) == Decimal("12.5")
    with pytest.raises(Problem) as too_old:  # rates older than the window don't count
        await _convert(engine, "1", "CHF", "EUR", day + service.WINDOW + timedelta(days=1))
    assert too_old.value.code == "no_rate"
    with pytest.raises(Problem):
        await _convert(engine, "1", "CHF", "EUR", day - timedelta(days=1))  # none yet


async def test_the_ecbs_rates_are_loaded_once_a_day(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, text=ECB)

    monkeypatch.setattr(service, "TEST_TRANSPORT", [httpx.MockTransport(answer)])
    async with acting(engine, None) as records:
        said = await service.load_ecb(records.conn)
    async with acting(engine, None) as records:
        again = await service.load_ecb(records.conn)
    assert said == "3 rates of 2026-11-13 recorded from the ECB."
    assert again == "0 rates of 2026-11-13 recorded from the ECB."
    assert asked == [service.ECB_URL, service.ECB_URL]
    chf = await _convert(engine, "100", "EUR", "CHF", DAY)
    assert chf == pytest.approx(Decimal("93.50"), rel=Decimal("1e-9"))


async def test_the_ecb_job_is_off_until_switched_on(engine: AsyncEngine) -> None:
    state, said = await run_job(engine, "sys.exchange_rates")
    assert state == "succeeded"
    assert said is not None and said.startswith("Off")


def test_a_currency_is_a_code_in_upper_case() -> None:
    currency = FIELD_TYPES["currency"]
    assert currency.check(" chf ", {}) == "CHF"
    assert currency.check("usdt", {}) == "USDT"
    for wrong in ("CH", "CHF1", "Swiss franc", 5):
        with pytest.raises(Invalid):
            currency.check(wrong, {})


@pytest.mark.parametrize(
    "options",
    [
        {"precision": 18, "scale": 2},  # no currency at all
        {"precision": 18, "scale": 2, "currency": "CHF", "currency_field": "c"},  # both
        {"precision": 18, "scale": 2, "currency": "francs"},
    ],
)
def test_an_amount_names_its_currency_one_way(options: dict[str, Any]) -> None:
    assert FIELD_TYPES["money"].option_problems(options)


def test_the_currency_field_of_an_amount_is_a_currency() -> None:
    wrong = Table(
        "cost",
        "Cost",
        {"amount": Money("Amount", currency_field="unit"), "unit": String("Unit", max_length=5)},
    )
    with pytest.raises(Problem) as refused:
        check("zz", [wrong])
    assert [e.code for e in refused.value.errors] == ["bad_currency_field"]


def test_a_decimal_can_become_an_amount() -> None:
    money = FIELD_TYPES["money"]
    before = DecimalField("Amount", precision=18, scale=2)
    after = Money("Amount", currency="CHF", precision=18, scale=2)
    assert money.keeps_values(before, after)
    assert not money.keeps_values(DecimalField("Amount", precision=18, scale=4), after)
    assert not money.keeps_values(Field("string", {"en": "x"}, {"max_length": 5}), after)


async def test_a_module_keeps_amounts_with_their_currency(
    owner_engine: AsyncEngine, engine: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> None:
    module_id = await new_module()
    cost = Table(
        "cost",
        "Cost",
        {
            "text": String("Text", max_length=60),
            "currency": Currency("Currency", required=True),
            "amount": Money("Amount", currency_field="currency", required=True),
            "fee": Money("Fee", currency="CHF", scale=2),
        },
    )
    module = Module(
        id=module_id,
        publisher="test",
        name="Costs",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(cost,),
    )
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, module])
    table = f"{module_id}.cost"
    async with acting(engine, None) as records:
        made = await records.create(
            table, {"text": "Oil", "currency": "eur", "amount": "1250.555", "fee": 3}
        )
        body = records.to_json(records.catalog.tables[table], made)
        await records.create(table, {"text": "Gas", "currency": "CHF", "amount": "80"})
        big = await records.list(table, where={"field": "amount", "op": "gt", "value": "100"})
        column = await records.conn.scalar(
            text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute"
                f" WHERE attrelid = '{module_id}.cost'::regclass AND attname = 'amount'"
            )
        )
    assert (body["currency"], body["amount"], body["fee"]) == ("EUR", "1250.56", "3.00")
    assert [r.values["text"] for r in big.records] == ["Oil"]
    assert column == "numeric(18,2)"
