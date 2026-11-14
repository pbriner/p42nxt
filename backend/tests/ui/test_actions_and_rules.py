"""UI actions and form rules: what a module declares is checked before it's written."""

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.ui.actions.models import ActionInput, ActionResult, ActionRun, UIAction
from p42cloud.ui.actions.service import write_actions
from p42cloud.ui.rules.models import FormRule
from p42cloud.ui.rules.service import write_rules


async def _nothing(run: ActionRun) -> ActionResult:
    return ActionResult()


@pytest.mark.parametrize(
    ("action", "code"),
    [
        (
            UIAction("x", "sys.person", "X", places=("somewhere",), set={"active": False}),
            "bad_places",
        ),
        (UIAction("x", "sys.person", "X"), "no_work"),
        (UIAction("x", "sys.person", "X", set={"active": False}, handler=_nothing), "no_work"),
        (UIAction("x", "sys.person", "X", set={"colour": "red"}), "unknown_field"),
        (UIAction("x", "sys.person", "X", set={"kind": "robot"}), "bad_value"),
        (UIAction("x", "sys.person", "X", set={"name": "@input.who"}), "unknown_input"),
        (
            UIAction(
                "x",
                "sys.person",
                "X",
                inputs=(ActionInput("n", "N", type="file"),),
                handler=_nothing,
            ),
            "bad_input",
        ),
        (UIAction("x", "hello.greeting", "X", set={"pinned": True}), "unknown_table"),
    ],
)
async def test_actions_are_checked(owner_engine: AsyncEngine, action: UIAction, code: str) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_actions(conn, await load_catalog(conn), "sys", (action,))
    assert code in [e.code for e in refused.value.errors]


@pytest.mark.parametrize(
    ("condition", "fields", "code"),
    [
        ({"field": "kind", "op": "eq", "value": "service"}, {"hide": ("colour",)}, "unknown_field"),
        ({"field": "created_by.name", "op": "eq", "value": "x"}, {}, "not_for_rules"),
        ({"field": "created", "op": "gt", "value": "@today"}, {}, "not_for_rules"),
        ({"field": "name", "op": "matches", "value": "x"}, {}, "not_for_rules"),
    ],
)
async def test_rules_use_what_the_browser_evaluates_too(
    owner_engine: AsyncEngine, condition: Any, fields: dict[str, Any], code: str
) -> None:
    rule = FormRule("x", "sys.person", condition, **fields)
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_rules(conn, await load_catalog(conn), "sys", (rule,))
    assert code in [e.code for e in refused.value.errors]
