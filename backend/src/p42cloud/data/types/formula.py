"""A calculated field: worked out by the database from the record's own numbers and dates on
every save (`formulas.py`), so lists filter, sort and add it up like any number. Never typed."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, declare
from p42cloud.data.types.decimal import DecimalType

_OWN = ("expression", "currency_field", "currency")


def amount_problems(options: dict[str, Any]) -> list[str]:
    """An optional currency, like an amount's: named by another field, or fixed."""
    field, fixed = options.get("currency_field"), options.get("currency")
    if field is not None and fixed is not None:
        return ["Name currency_field or currency, not both."]
    if fixed is not None and not (isinstance(fixed, str) and len(fixed) == 3 and fixed.isupper()):  # noqa: PLR2004
        return ["currency must be a currency code, like CHF."]
    if field is not None and not isinstance(field, str):
        return ["currency_field must name a field of the table."]
    return []


class FormulaType(DecimalType):
    name = "formula"
    label = "Calculated number"
    generated = True

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        found = super().option_problems({k: v for k, v in options.items() if k not in _OWN})
        expression = options.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            found.append("expression must be a formula, like amount * quantity.")
        return found + amount_problems(options)

    def keeps_values(self, old: Field, new: Field) -> bool:
        # Worked out again on the next save: any formula may become any other.
        return old.type == "formula"


TYPE = FormulaType()


def Formula(  # noqa: PLR0913 - the field's options, all by keyword
    label: str,
    expression: str,
    *,
    precision: int = 18,
    scale: int = 2,
    currency_field: str | None = None,
    currency: str | None = None,
    **common: Unpack[Common],
) -> Field:
    """`Formula("Total", "amount * quantity")`: numbers and dates of the same record, + - * /,
    round, abs, coalesce, least, greatest. Two dates subtracted give days."""
    options: dict[str, Any] = {"expression": expression, "precision": precision, "scale": scale}
    if currency_field is not None:
        options["currency_field"] = currency_field
    if currency is not None:
        options["currency"] = currency
    return declare("formula", label, options, common)
