"""An amount of money: an exact number, in a currency named by another field of the record
(`currency_field`) or fixed by the declaration (`currency`). Sorted, filtered and summed as a
number; shown with its currency."""

import re
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, declare
from p42cloud.data.types.currency import CODE
from p42cloud.data.types.decimal import DecimalType


class MoneyType(DecimalType):
    name = "money"
    label = "Amount with a currency"

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        own = ("currency_field", "currency")
        found = super().option_problems({k: v for k, v in options.items() if k not in own})
        field, fixed = options.get("currency_field"), options.get("currency")
        if (field is None) == (fixed is None):
            found.append("Name either currency_field (a currency field) or currency (a code).")
        elif fixed is not None and not (isinstance(fixed, str) and re.fullmatch(CODE, fixed)):
            found.append("currency must be a currency code, like CHF.")
        elif field is not None and not isinstance(field, str):
            found.append("currency_field must name a field of the table.")
        return found

    def keeps_values(self, old: Field, new: Field) -> bool:
        # A plain decimal can become an amount with a currency, and back within money.
        if old.type not in ("decimal", "money"):
            return False
        as_decimal = Field(
            "decimal", old.label, {k: old.options[k] for k in ("precision", "scale")}
        )
        mine = Field("decimal", new.label, {k: new.options[k] for k in ("precision", "scale")})
        return super().keeps_values(as_decimal, mine)


TYPE = MoneyType()


def Money(
    label: str,
    *,
    currency_field: str | None = None,
    currency: str | None = None,
    precision: int = 18,
    scale: int = 2,
    **common: Unpack[Common],
) -> Field:
    options: dict[str, Any] = {"precision": precision, "scale": scale}
    if currency_field is not None:
        options["currency_field"] = currency_field
    if currency is not None:
        options["currency"] = currency
    return declare("money", label, options, common)
