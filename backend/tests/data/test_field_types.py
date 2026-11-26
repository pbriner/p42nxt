"""Field types: how values are checked and stored, and which changes keep every value."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as Number
from typing import Any

import pytest

from p42cloud.data.schema import widens
from p42cloud.data.types import FIELD_TYPES, Field, Invalid
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text


def test_every_type_is_found_by_name() -> None:
    assert sorted(FIELD_TYPES) == [
        "any_record",
        "boolean",
        "choice",
        "color",
        "currency",
        "date",
        "datetime",
        "decimal",
        "duration",
        "email",
        "formula",
        "hostname",
        "icon",
        "integer",
        "json",
        "markdown",
        "money",
        "number",
        "percent",
        "phone",
        "reference",
        "rollup",
        "secret",
        "string",
        "text",
        "translated_text",
        "url",
    ]


@pytest.mark.parametrize(
    ("type_name", "options", "given", "stored"),
    [
        ("string", {"max_length": 5}, "Desk", "Desk"),
        ("translated_text", {}, "Desk", {"en": "Desk"}),
        ("translated_text", {}, {"en": "Desk", "de": "Pult"}, {"en": "Desk", "de": "Pult"}),
        ("email", {}, " anna@example.com ", "anna@example.com"),
        ("hostname", {}, "Mail.Example.COM.", "mail.example.com"),
        ("color", {}, "#1A73E8", "#1a73e8"),
        ("integer", {}, 42, 42),
        ("decimal", {"precision": 6, "scale": 2}, "12.345", Number("12.35")),
        ("decimal", {"precision": 6, "scale": 2}, 0.1, Number("0.10")),
        ("percent", {}, "0.05", Number("0.050000")),
        ("boolean", {}, True, True),
        ("date", {}, "2026-11-13", date(2026, 11, 13)),
        (
            "datetime",
            {},
            "2026-11-13T10:30:00+01:00",
            datetime(2026, 11, 13, 9, 30, tzinfo=UTC),
        ),
        ("duration", {}, "P1DT2H30M", timedelta(days=1, hours=2, minutes=30)),
        ("choice", {"values": [{"value": "new", "label": {"en": "New"}}]}, "new", "new"),
        (
            "reference",
            {"table": "sys.table"},
            "01900000-0000-7000-8000-000000000001",
            uuid.UUID("01900000-0000-7000-8000-000000000001"),
        ),
        ("json", {}, {"a": [1, 2]}, {"a": [1, 2]}),
        ("json", {"lines": True}, [" 192.168.1.0/24 ", ""], ["192.168.1.0/24"]),
        ("json", {"lines": True}, "a\nb, c;d", ["a", "b", "c", "d"]),
        ("json", {"lines": True}, "\n ,", None),
        ("text", {}, None, None),
    ],
)
def test_values_are_checked_and_stored_in_their_form(
    type_name: str, options: dict[str, Any], given: Any, stored: Any
) -> None:
    assert FIELD_TYPES[type_name].check(given, options) == stored


@pytest.mark.parametrize(
    ("type_name", "options", "given"),
    [
        ("string", {"max_length": 3}, "Desk"),
        ("string", {"max_length": 3}, 12),
        ("translated_text", {}, {"english": "Desk"}),
        ("email", {}, "anna"),
        ("url", {}, "javascript:alert(1)"),
        ("color", {}, "red"),
        ("integer", {}, True),
        ("integer", {}, 1.5),
        ("integer", {}, 2**63),
        ("decimal", {"precision": 4, "scale": 2}, "123.4"),
        ("decimal", {"precision": 4, "scale": 2}, "NaN"),
        ("boolean", {}, "yes"),
        ("date", {}, "13.11.2026"),
        ("datetime", {}, "2026-11-13T10:30:00"),
        ("duration", {}, "P1M"),
        ("duration", {}, "PT"),
        ("choice", {"values": [{"value": "new", "label": {"en": "New"}}]}, "old"),
        ("reference", {"table": "sys.table"}, "not an id"),
        ("json", {}, float("nan")),
        ("json", {"lines": True}, {"a": 1}),
        ("json", {"lines": True}, ["a", 2]),
    ],
)
def test_wrong_values_are_refused(type_name: str, options: dict[str, Any], given: Any) -> None:
    with pytest.raises(Invalid):
        FIELD_TYPES[type_name].check(given, options)


def test_options_are_checked() -> None:
    assert FIELD_TYPES["string"].option_problems({"max_length": 0})
    assert FIELD_TYPES["string"].option_problems({"max_length": 10, "colour": "red"})
    assert FIELD_TYPES["decimal"].option_problems({"precision": 4, "scale": 5})
    assert FIELD_TYPES["choice"].option_problems({"values": []})
    assert FIELD_TYPES["choice"].option_problems(
        {"values": [{"value": "a"}, {"value": "a"}, {"value": "B!"}]}
    )
    assert FIELD_TYPES["choice"].option_problems(
        {"values": [{"value": "done", "label": {"en": "Done"}, "tone": "red"}]}
    )
    toned = Choice("State", {"open": "Open", "done": "Done"}, tones={"done": "primary"})
    assert FIELD_TYPES["choice"].option_problems(toned.options) == []
    assert [v.get("tone") for v in toned.options["values"]] == [None, "primary"]
    assert FIELD_TYPES["reference"].option_problems({"table": "no dots"})
    assert FIELD_TYPES["text"].option_problems({}) == []


@pytest.mark.parametrize(
    ("old", "new", "keeps"),
    [
        (String("A", max_length=10), String("A", max_length=20), True),
        (String("A", max_length=20), String("A", max_length=10), False),
        (String("A", max_length=20), Text("A"), True),
        (Text("A"), String("A", max_length=20), False),
        (Integer("A"), Decimal("A", precision=21, scale=2), True),
        (Integer("A"), Decimal("A", precision=18, scale=2), False),
        (Decimal("A", precision=8, scale=2), Decimal("A", precision=10, scale=3), True),
        (Decimal("A", precision=8, scale=2), Decimal("A", precision=8, scale=1), False),
        (Choice("A", {"a": "A"}), Choice("A", {"a": "A", "b": "B"}), True),
        (Choice("A", {"a": "A", "b": "B"}), Choice("A", {"a": "A"}), False),
        (String("A"), Integer("A"), False),
    ],
)
def test_changes_that_keep_every_value(old: Field, new: Field, keeps: bool) -> None:
    assert FIELD_TYPES[new.type].keeps_values(old, new) is keeps


@pytest.mark.parametrize(
    ("old", "new", "fits"),
    [
        ("character varying(10)", "character varying(20)", True),
        ("character varying(20)", "character varying(10)", False),
        ("character varying(20)", "text", True),
        ("text", "character varying(20)", False),
        ("bigint", "numeric(21,2)", True),
        ("bigint", "numeric(18,2)", False),
        ("numeric(8,2)", "numeric(10,3)", True),
        ("numeric(8,2)", "numeric(8,3)", False),
        ("text", "bigint", False),
        ("jsonb", "jsonb", True),
    ],
)
def test_the_engine_widens_columns_only(old: str, new: str, fits: bool) -> None:
    assert widens(old, new) is fits


def test_a_json_field_can_become_lines_and_back() -> None:
    plain = Field("json", {"en": "Ranges"})
    lines = Field("json", {"en": "Ranges"}, {"lines": True})
    kind = FIELD_TYPES["json"]
    assert kind.keeps_values(plain, lines)
    assert kind.keeps_values(lines, plain)
    assert kind.option_problems({"lines": True}) == []
    assert kind.option_problems({"lines": "yes"}) == ["lines is true or false."]
