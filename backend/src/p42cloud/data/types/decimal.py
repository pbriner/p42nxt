"""A number with a fixed number of decimals; never a float."""

from decimal import ROUND_HALF_UP, InvalidOperation
from decimal import Decimal as Number
from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, FieldType, Invalid, declare

# Digits a bigint can have, so a whole number fits a decimal with at least as many before the
# point.
INTEGER_DIGITS = 19


def number(value: Any) -> Number:
    """A decimal number from a JSON number or text; floats only through their text, never binary."""
    if isinstance(value, bool) or not isinstance(value, int | float | str | Number):
        raise Invalid("Must be a number.")
    try:
        result = Number(str(value))
    except InvalidOperation as error:
        raise Invalid("Must be a number.") from error
    if not result.is_finite():
        raise Invalid("Must be a number.")
    return result


def fit(value: Number, precision: int, scale: int) -> Number:
    # Half away from zero, as PostgreSQL rounds numeric: the same value either way in.
    rounded = value.quantize(Number(1).scaleb(-scale), rounding=ROUND_HALF_UP)
    if rounded.adjusted() >= precision - scale:
        raise Invalid(f"Must have at most {precision - scale} digits before the point.")
    return rounded


class DecimalType(FieldType):
    name = "decimal"
    label = "Decimal number"
    kind = "number"

    def to_json(self, value: Any) -> Any:
        return None if value is None else str(value)

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        rest = {k: v for k, v in options.items() if k not in ("precision", "scale")}
        found = super().option_problems(rest)
        precision, scale = options.get("precision"), options.get("scale")
        if not isinstance(precision, int) or not 1 <= precision <= 38:  # noqa: PLR2004
            found.append("precision must be a whole number from 1 to 38.")
        elif not isinstance(scale, int) or not 0 <= scale <= precision:
            found.append("scale must be a whole number from 0 to the precision.")
        return found

    def column_type(self, options: dict[str, Any]) -> str:
        return f"numeric({int(options['precision'])},{int(options['scale'])})"

    def check(self, value: Any, options: dict[str, Any]) -> Any:
        if value is None:
            return None
        return fit(number(value), options["precision"], options["scale"])

    def keeps_values(self, old: Field, new: Field) -> bool:
        before = new.options["precision"] - new.options["scale"]
        if old.type == "integer":
            return before >= INTEGER_DIGITS
        if old.type != "decimal":
            return False
        old_before = old.options["precision"] - old.options["scale"]
        return new.options["scale"] >= old.options["scale"] and before >= old_before


TYPE = DecimalType()


def Decimal(label: str, *, precision: int = 18, scale: int = 2, **common: Unpack[Common]) -> Field:
    return declare("decimal", label, {"precision": precision, "scale": scale}, common)
