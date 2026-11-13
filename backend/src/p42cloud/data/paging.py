"""Paging by cursor (keyset paging): a page's link holds the sort values of its last record.

The next page starts after that record in the same order, so pages stay fast on large tables
and never skip or repeat a record when others change the table. Empty values sort last, and
ties are broken by `sys_id`, so the order is always the same.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from p42cloud.data.catalog import FieldDef
from p42cloud.data.conditions import Query
from p42cloud.data.types import Invalid
from p42cloud.kernel.errors import Problem


@dataclass(frozen=True)
class Sort:
    field: FieldDef
    descending: bool
    expression: str  # the SQL sorted by: the value, or empty where it may not be read


def _value(s: Sort, values: dict[str, Any], language: str) -> Any:
    value = values.get(s.field.name)
    if s.field.type.kind == "translated":
        return None if value is None else value.get(language, value.get("en"))
    return s.field.type.to_json(value)


def cursor(sort: list[str], sorting: list[Sort], values: dict[str, Any], language: str) -> str:
    """The link to the page after the record with these values."""
    state = {
        "sort": sort,
        "values": [_value(s, values, language) for s in sorting],
        "id": str(values["sys_id"]),
    }
    return base64.urlsafe_b64encode(json.dumps(state).encode()).decode()


def _parse(s: Sort, raw: Any) -> Any:
    if raw is None:
        return None
    if s.field.type.kind == "translated":
        if not isinstance(raw, str):
            raise Invalid("Not text.")
        return raw
    return s.field.type.check(raw, s.field.field.options)


def after_cursor(link: str, sort: list[str], sorting: list[Sort], query: Query) -> str:
    """SQL: the rows after the cursor's row in this order. A link of another order is refused."""
    bad = Problem(400, "bad_cursor", "The page link isn't valid; start from the first page.")
    try:
        state = json.loads(base64.urlsafe_b64decode(link.encode()))
        if state["sort"] != sort or len(state["values"]) != len(sorting):
            raise bad
        values = [_parse(s, raw) for s, raw in zip(sorting, state["values"], strict=True)]
        last = UUID(state["id"])
    except (ValueError, KeyError, TypeError, binascii.Error, Invalid) as error:
        raise bad from error
    options: list[str] = []
    equal: list[str] = []
    for s, value in zip(sorting, values, strict=True):
        x = s.expression
        if value is None:
            options.append(f"({' AND '.join([*equal, 'false'])})")
            equal.append(f"{x} IS NULL")
            continue
        bound = query.bind(value)
        beyond = f"({x} {'<' if s.descending else '>'} {bound} OR {x} IS NULL)"
        options.append(f"({' AND '.join([*equal, beyond])})")
        equal.append(f"{x} = {bound}")
    options.append(f"({' AND '.join([*equal, f't.sys_id > {query.bind(last)}'])})")
    return f"({' OR '.join(options)})"
