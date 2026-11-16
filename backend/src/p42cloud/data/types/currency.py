"""A currency by its code: ISO 4217 (CHF, EUR, USD) or a common crypto code (BTC, USDT), in
upper case. The currencies an installation knows are records of `sys.currency`."""

from typing import Unpack

from p42cloud.data.types.base import Common, Field, PatternType, declare

CODE = r"[A-Z]{3,5}"


class CurrencyType(PatternType):
    name = "currency"
    label = "Currency"
    pattern = CODE
    column = "character varying(5)"
    message = "Must be a currency code, like CHF or EUR."

    def normalize(self, value: str) -> str:
        return value.strip().upper()


TYPE = CurrencyType()


def Currency(label: str, **common: Unpack[Common]) -> Field:
    return declare("currency", label, {}, common)
