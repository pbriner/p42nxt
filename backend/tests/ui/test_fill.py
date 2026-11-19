"""Defaults worked out on each create (@me, @today+14d, @now), and form rules that fill empty
fields, before the table's own hooks see the record."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import timedelta
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table, check
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.presenting import describe
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.date import Date
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.ui.rules.models import FormRule
from tests.security.helpers import acting, change


async def _note_what_hooks_see(change: Change) -> None:
    """The table's own hook: it sees what rules filled."""
    if change.operation == "create" and change.values.get("category") == "food":
        change.values["note"] = "seen as food"


def _module(module_id: str) -> Module:
    m = module_id
    booking = Table(
        "booking",
        "Booking",
        {
            "text": String("Text", max_length=80),
            "category": Choice("Category", {"food": "Food", "fuel": "Fuel"}),
            "center": Reference("Center", f"{m}.center"),
            "note": String("Note", max_length=80),
            "due": Date("Due", default="@today+14d"),
            "owner": Reference("Owner", "sys.person", default="@me"),
            "logged": DateTime("Logged", default="@now"),
        },
    )
    return Module(
        id=m,
        publisher="test",
        name="Fill",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(Table("center", "Center", {"name": String("Name", max_length=40)}), booking),
        hooks=(TableHook(f"{m}.booking", before=_note_what_hooks_see),),
        rules=(
            FormRule(
                "agrola",
                f"{m}.booking",
                {"field": "text", "op": "matches", "value": "agrola|top ?shop"},
                fill={"category": "food"},
            ),
        ),
    )


@pytest.fixture(scope="module")
async def app(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def module_id(owner_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]]) -> str:
    m = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(m)])
    return m


async def test_defaults_are_worked_out_on_each_create(app: AsyncEngine, module_id: str) -> None:
    table = f"{module_id}.booking"
    async with acting(app, None) as records:
        made = await records.create(table, {"text": "Lunch"})
        situation = records.situation
        fields = {
            f["name"]: f
            for f in describe(
                records.catalog.tables[table], records.access, "en", situation=situation
            )["fields"]
        }
    assert made.values["due"] == situation.today + timedelta(days=14)
    assert made.values["owner"] == situation.person
    assert made.values["logged"] is not None
    # A form shows them worked out for the person, and the word.
    assert fields["due"]["default"] == (situation.today + timedelta(days=14)).isoformat()
    assert fields["due"]["default_rule"] == "@today+14d"
    assert fields["owner"]["default"] == str(situation.person)


async def test_a_rule_fills_empty_fields_before_the_tables_hooks(
    app: AsyncEngine, module_id: str
) -> None:
    table = f"{module_id}.booking"
    async with acting(app, None) as records:
        shop = await records.create(table, {"text": "AGROLA TopShop Uster"})
        fuel = await records.create(table, {"text": "Agrola diesel", "category": "fuel"})
        other = await records.create(table, {"text": "Bakery"})
    assert (shop.values["category"], shop.values["note"]) == ("food", "seen as food")
    assert fuel.values["category"] == "fuel"  # given: never overwritten
    assert other.values["category"] is None

    # An administrator's rule as a record, filling a reference; on a change of its condition.
    async with acting(app, None) as records:
        center = await records.create(f"{module_id}.center", {"name": "Food"})
        await records.create(
            "sys.form_rule",
            {
                "name": f"{module_id}.food_center",
                "module": module_id,
                "table": table,
                "condition": {"field": "category", "op": "eq", "value": "food"},
                "fill": {"center": str(center.sys_id)},
                "custom": True,
            },
        )
    async with acting(app, None) as records:
        await change(records, table, other.sys_id, {"category": "food"})
        changed = await records.read(table, other.sys_id)
        await change(records, table, other.sys_id, {"text": "Bakery Meier"})
    assert changed.values["center"] == center.sys_id


@pytest.mark.parametrize(
    ("fill", "condition"),
    [
        ({"colour": "red"}, None),
        ({"category": "gold"}, None),
        ({"category": "food"}, {"field": "text", "op": "matches", "value": "(a)\\1"}),
    ],
)
async def test_an_administrators_rule_is_checked(
    app: AsyncEngine, module_id: str, fill: dict[str, Any], condition: Any
) -> None:
    with pytest.raises(Problem) as refused:
        async with acting(app, None) as records:
            await records.create(
                "sys.form_rule",
                {
                    "name": f"{module_id}.bad",
                    "module": module_id,
                    "table": f"{module_id}.booking",
                    "condition": condition,
                    "fill": fill,
                    "custom": True,
                },
            )
    assert {e.code for e in refused.value.errors} & {"bad_fill", "not_for_rules", "bad_value"}


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        (Reference("Owner", "sys.group", default="@me"), "@me fills in a reference to a person"),
        (Date("Due", default="@tomorrow"), "variable"),
    ],
)
def test_a_default_word_must_fit_its_field(declared: Any, problem: str) -> None:
    with pytest.raises(Problem) as refused:
        check("zz", [Table("t", "T", {"f": declared})])
    assert any(problem in e.message for e in refused.value.errors)
