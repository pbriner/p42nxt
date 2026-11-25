"""Calculated fields: a small arithmetic language over a record's own numbers and dates, turned
into SQL the database works out on every save (`types/formula.py`, trigger `p42_formula`).

    amount * quantity
    round(net * (1 + vat_rate), 2)
    coalesce(discount, 0)
    due - invoiced                      (days between two dates)

Numbers, the record's number and date fields, + - * /, parentheses and the functions round, abs,
coalesce, least and greatest. A division by zero gives nothing (empty), as does an empty field
unless coalesce gives a value instead. The result is a number; two dates subtracted give days.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

from p42cloud.data.sql import ident
from p42cloud.data.types.base import Invalid

MAX_LENGTH = 500
MAX_DEPTH = 20
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([a-z][a-z0-9_]*)|(.))")
_FUNCTIONS = {
    "round": (1, 2),
    "abs": (1, 1),
    "coalesce": (2, 5),
    "least": (2, 5),
    "greatest": (2, 5),
}


@dataclass(frozen=True)
class Typed:
    sql: str
    kind: str  # number or date


class _Parser:
    def __init__(self, expression: str, kinds: dict[str, str]) -> None:
        self.tokens = list(self._split(expression))
        self.at = 0
        self.kinds = kinds  # field name -> number or date
        self.used: set[str] = set()

    @staticmethod
    def _split(expression: str) -> Iterator[tuple[str, str]]:
        position = 0
        while position < len(expression):
            found = _TOKEN.match(expression, position)
            if found is None or found.end() == position:
                break
            position = found.end()
            number, name, other = found.groups()
            if number is not None:
                yield "number", number
            elif name is not None:
                yield "name", name
            elif other is not None and other.strip():
                if other not in "+-*/(),":
                    raise Invalid(f"{other!r} isn't part of a formula.")
                yield "symbol", other

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, symbol: str | None = None) -> tuple[str, str]:
        token = self.peek()
        if token is None or (symbol is not None and token != ("symbol", symbol)):
            raise Invalid(f"Expected {symbol!r}." if symbol else "The formula ends too early.")
        self.at += 1
        return token

    def whole(self) -> Typed:
        if not self.tokens:
            raise Invalid("A formula can't be empty.")
        found = self.sum(0)
        if self.peek() is not None:
            raise Invalid(f"Unexpected {self.peek()[1]!r}.")  # type: ignore[index]
        return found

    def sum(self, depth: int) -> Typed:
        left = self.product(depth)
        while self.peek() in (("symbol", "+"), ("symbol", "-")):
            op = self.take()[1]
            right = self.product(depth)
            left = _arithmetic(left, op, right)
        return left

    def product(self, depth: int) -> Typed:
        left = self.factor(depth)
        while self.peek() in (("symbol", "*"), ("symbol", "/")):
            op = self.take()[1]
            right = self.factor(depth)
            left = _arithmetic(left, op, right)
        return left

    def factor(self, depth: int) -> Typed:
        if depth > MAX_DEPTH:
            raise Invalid(f"A formula may nest at most {MAX_DEPTH} deep.")
        kind, value = self.take()
        if (kind, value) == ("symbol", "-"):
            inner = self.factor(depth + 1)
            _numbers_only(inner, "-")
            return Typed(f"(-{inner.sql})", "number")
        if (kind, value) == ("symbol", "("):
            inner = self.sum(depth + 1)
            self.take(")")
            return Typed(f"({inner.sql})", inner.kind)
        if kind == "number":
            return Typed(f"{value}::numeric", "number")
        if kind == "name" and self.peek() == ("symbol", "("):
            return self.call(value, depth)
        if kind == "name":
            if value not in self.kinds:
                raise Invalid(f"{value!r} isn't a number or date field of this table.")
            self.used.add(value)
            return Typed(f"r.{ident(value)}", self.kinds[value])
        raise Invalid(f"Unexpected {value!r}.")

    def call(self, name: str, depth: int) -> Typed:
        if name not in _FUNCTIONS:
            raise Invalid(f"{name!r} isn't a function formulas know ({', '.join(_FUNCTIONS)}).")
        self.take("(")
        arguments = [self.sum(depth + 1)]
        while self.peek() == ("symbol", ","):
            self.take(",")
            arguments.append(self.sum(depth + 1))
        self.take(")")
        low, high = _FUNCTIONS[name]
        if not low <= len(arguments) <= high:
            raise Invalid(f"{name} takes {low} to {high} values.")
        if name == "round" and len(arguments) == 2:  # noqa: PLR2004
            places = arguments[1].sql.removesuffix("::numeric")
            if not places.isdigit():
                raise Invalid("round's second value is a whole number of places, like 2.")
            _numbers_only(arguments[0], name)
            return Typed(f"round({arguments[0].sql}, {places})", "number")
        kinds = {a.kind for a in arguments}
        if len(kinds) > 1:
            raise Invalid(f"{name} takes numbers or dates, not both.")
        if name in ("round", "abs"):
            _numbers_only(arguments[0], name)
        return Typed(f"{name}({', '.join(a.sql for a in arguments)})", kinds.pop())


def _numbers_only(value: Typed, where: str) -> None:
    if value.kind != "number":
        raise Invalid(f"{where} takes numbers, not dates.")


def _arithmetic(left: Typed, op: str, right: Typed) -> Typed:
    if left.kind == "date" or right.kind == "date":
        if op == "-" and left.kind == right.kind == "date":
            return Typed(f"({left.sql} - {right.sql})::numeric", "number")
        raise Invalid("Dates can only be subtracted from each other (which gives days).")
    if op == "/":
        return Typed(f"({left.sql} / NULLIF({right.sql}, 0))", "number")
    return Typed(f"({left.sql} {op} {right.sql})", "number")


def compile_formula(expression: str, kinds: dict[str, str]) -> tuple[str, frozenset[str]]:
    """The SQL for a formula over a record `r`, and the fields it uses; Invalid if it's wrong.

    `kinds` names the fields it may use: number or date."""
    if not isinstance(expression, str) or len(expression) > MAX_LENGTH:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise Invalid(f"A formula is text of at most {MAX_LENGTH} characters.")
    parser = _Parser(expression.strip(), kinds)
    found = parser.whole()
    if found.kind != "number":
        raise Invalid("A formula gives a number; subtract two dates to get days.")
    return found.sql, frozenset(parser.used)
