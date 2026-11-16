"""Currencies and their exchange rates as records."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.currency import Currency
from p42cloud.data.types.date import Date
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

CURRENCY = Table(
    "currency",
    "Currency",
    {
        "name": Currency("Code", required=True, unique=True),
        "label": TranslatedText("Name", required=True),
        "decimals": Integer("Decimals", required=True, default=2),
        "active": Boolean("Offered", required=True, default=True),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Currencies",
    display="label",
)

EXCHANGE_RATE = Table(
    "exchange_rate",
    "Exchange rate",
    {
        "date": Date("Date", required=True),
        # One unit of `currency` is worth `rate` units of `base`.
        "currency": Currency("Currency", required=True),
        "base": Currency("Base currency", required=True),
        "rate": Decimal("Rate", precision=24, scale=12, required=True),
        "origin": String("From", max_length=40),
    },
    label_plural="Exchange rates",
    unique_together=(("date", "currency", "base"),),
    display="currency",
)

TABLES = [CURRENCY, EXCHANGE_RATE]
