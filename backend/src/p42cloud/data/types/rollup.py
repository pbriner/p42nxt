"""A roll-up: the sum or count of the records pointing to this one (a statement's lines), kept
right by the database whenever one of them is made, changed, moved, deleted or restored
(trigger `p42_rollup` on the other table). Lists filter, sort and add it up like any number."""

from typing import Any, Unpack

from p42cloud.data.types.base import Common, Field, declare
from p42cloud.data.types.decimal import DecimalType
from p42cloud.data.types.formula import amount_problems

MEASURES = ("sum", "count")
_OWN = ("table", "via", "measure", "field", "where", "currency_field", "currency")


class RollupType(DecimalType):
    name = "rollup"
    label = "Roll-up of related records"
    generated = True

    def option_problems(self, options: dict[str, Any]) -> list[str]:
        found = super().option_problems({k: v for k, v in options.items() if k not in _OWN})
        table, via = options.get("table"), options.get("via")
        measure, field = options.get("measure"), options.get("field")
        if not isinstance(table, str) or "." not in table:
            found.append("table must name the related table, like finance.statement_line.")
        if not isinstance(via, str):
            found.append("via must name the related table's reference to this one.")
        if measure not in MEASURES:
            found.append(f"measure must be one of {', '.join(MEASURES)}.")
        elif measure == "sum" and not isinstance(field, str):
            found.append("A sum needs field: the related table's number to add up.")
        elif measure == "count" and field is not None:
            found.append("A count takes no field.")
        where = options.get("where")
        if where is not None and not (
            isinstance(where, dict) and all(isinstance(k, str) for k in where)  # pyright: ignore[reportUnknownVariableType]
        ):
            found.append("where maps fields of the related table to the values they must have.")
        return found + amount_problems(options)

    def keeps_values(self, old: Field, new: Field) -> bool:
        # Worked out again for every record when it changes.
        return old.type == "rollup"


TYPE = RollupType()


def Rollup(  # noqa: PLR0913 - the field's options, all by keyword
    label: str,
    table: str,
    via: str,
    *,
    measure: str = "sum",
    field: str | None = None,
    where: dict[str, Any] | None = None,
    precision: int = 18,
    scale: int = 2,
    currency_field: str | None = None,
    currency: str | None = None,
    **common: Unpack[Common],
) -> Field:
    """`Rollup("Total", "finance.statement_line", "statement", field="amount")`: the sum of the
    lines' amounts; `measure="count"` counts them. `where={"kind": "charge"}` counts only those
    whose fields have these values (deleted ones never count)."""
    options: dict[str, Any] = {
        "table": table,
        "via": via,
        "measure": measure,
        "precision": precision,
        "scale": scale,
    }
    for key, value in (
        ("field", field),
        ("where", where),
        ("currency_field", currency_field),
        ("currency", currency),
    ):
        if value is not None:
            options[key] = value
    return declare("rollup", label, options, common)
