"""What currencies add to the core: their tables, the main currency, the ECB's rates."""

from typing import Any

from p42cloud.data.settings import Setting, value
from p42cloud.data.types import Invalid
from p42cloud.data.types.currency import TYPE as CURRENCY
from p42cloud.services.currencies import models
from p42cloud.services.currencies.service import load_ecb
from p42cloud.services.jobs.models import Job, JobRun, Schedule

TABLES = models.TABLES


def _code(given: Any) -> str:
    try:
        return str(CURRENCY.check(given, {}))
    except Invalid as error:
        raise Invalid("A currency code, like CHF.") from error


def _on_off(given: Any) -> bool:
    if not isinstance(given, bool):
        raise Invalid("On or off.")
    return given


MAIN = Setting(
    "sys.main_currency",
    "Main currency",
    "CHF",
    check=_code,
    help="The currency amounts are shown and summed in when they come in several.",
    public=True,
)
ECB = Setting(
    "sys.exchange_rates_ecb",
    "Exchange rates from the ECB",
    False,
    check=_on_off,
    help="Loads the European Central Bank's rates every working day (from www.ecb.europa.eu;"
    " nothing is sent there). Off: rates are entered by hand.",
)
SETTINGS = (MAIN, ECB)


async def _load(run: JobRun) -> str:
    conn = run.records.conn
    if not await value(conn, ECB.name):
        return "Off (setting Exchange rates from the ECB): nothing loaded."
    return await load_ecb(conn)


JOBS = (Job("exchange_rates", "Load the ECB's exchange rates", _load, timeout=120, retries=3),)
SCHEDULES = (
    Schedule(
        "exchange_rates",
        "Exchange rates, working days",
        "exchange_rates",
        "30 16 * * 1-5",
        "Europe/Zurich",
    ),
)
