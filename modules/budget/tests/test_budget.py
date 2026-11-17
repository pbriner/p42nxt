"""Budget on its own installation (with Costing, and Finance for the actuals): versions and their
flow, lines only in drafts, spreading a year, plan against actual."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

Acting = Callable[..., AbstractAsyncContextManager[Client]]
CallRoute = Callable[..., Awaitable[Any]]


def _code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


async def _person(client: Client, role: str) -> str:
    found = await client.list("sys.role", where={"field": "name", "op": "eq", "value": role})
    group = await client.create("sys.group", {"name": f"{role} {uuid.uuid4().hex[:6]}"})
    await client.create(
        "sys.group_role", {"group": group["sys_id"], "role": found["records"][0]["sys_id"]}
    )
    person = await client.create("sys.person", {"name": f"{role} {uuid.uuid4().hex[:4]}"})
    await client.create("sys.membership", {"group": group["sys_id"], "person": person["sys_id"]})
    return str(person["sys_id"])


async def _setup(client: Client) -> dict[str, Any]:
    made: dict[str, Any] = {}
    house = await client.create(
        "costing.center", {"code": _code("H"), "name": "House", "kind": "profit"}
    )
    for name in ("Heating", "Garden"):
        made[name] = await client.create(
            "costing.center", {"code": _code(name[:2]), "name": name, "parent": house["sys_id"]}
        )
    made["energy"] = await client.create(
        "costing.cost_type", {"code": _code("EN"), "name": "Energy"}
    )
    made["house"] = house
    return made


async def test_a_version_is_submitted_approved_and_locked_by_a_controller(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        planner = await _person(client, "budget.planner")
        controller = await _person(client, "budget.controller")
    async with as_person(planner) as client:
        version = await client.create("budget.version", {"name": "Budget 2027", "year": 2027})
        assert version["state"] == "draft"
        await client.action("budget.version", version["sys_id"], "budget.move_submit")
        with pytest.raises(Problem):  # approving is a controller's
            current = await client.get("budget.version", version["sys_id"])
            await client.update(
                "budget.version", version["sys_id"], {"state": "approved"}, current["_etag"]
            )
    async with as_person(controller) as client:
        await client.action("budget.version", version["sys_id"], "budget.move_approve")
        await client.action("budget.version", version["sys_id"], "budget.move_lock")
        assert (await client.get("budget.version", version["sys_id"]))["state"] == "locked"


async def test_a_year_is_spread_and_lines_change_only_in_drafts(as_system: Acting) -> None:
    async with as_system() as client:
        made = await _setup(client)
        version = await client.create("budget.version", {"name": "Plan", "year": 2027})
        said = await client.action(
            "budget.version",
            version["sys_id"],
            "budget.spread",
            {
                "center": made["Heating"]["code"],
                "cost_type": made["energy"]["code"],
                "amount": "1000",
            },
        )
        lines = await client.list(
            "budget.line",
            where={"field": "version", "op": "eq", "value": version["sys_id"]},
            limit=20,
        )
        amounts = sorted((int(r["month"]), r["amount"]) for r in lines["records"])
        assert amounts[0] == (1, "83.33") and amounts[11] == (12, "83.37")  # cents left in December
        assert sum(float(a) for _, a in amounts) == pytest.approx(1000)
        assert "83.33 a month" in said["message"]
        # Spreading again replaces, doesn't add.
        await client.action(
            "budget.version",
            version["sys_id"],
            "budget.spread",
            {
                "center": made["Heating"]["code"],
                "cost_type": made["energy"]["code"],
                "amount": "1200",
            },
        )
        again = await client.list(
            "budget.line",
            where={"field": "version", "op": "eq", "value": version["sys_id"]},
            limit=20,
        )
        assert len(again["records"]) == 12 and again["records"][0]["amount"] == "100.00"
        await client.action("budget.version", version["sys_id"], "budget.move_submit")
    with pytest.raises(Problem) as refused:
        async with as_system() as client:
            await client.create(
                "budget.line",
                {
                    "version": version["sys_id"],
                    "center": made["Garden"]["sys_id"],
                    "cost_type": made["energy"]["sys_id"],
                    "month": 1,
                    "amount": "5",
                },
            )
    assert [e.field for e in refused.value.errors] == ["version"]
    with pytest.raises(Problem) as on_profit:
        async with as_system() as client:
            draft = await client.create("budget.version", {"name": "Other", "year": 2027})
            await client.create(
                "budget.line",
                {
                    "version": draft["sys_id"],
                    "center": made["house"]["sys_id"],
                    "cost_type": made["energy"]["sys_id"],
                    "month": 1,
                    "amount": "5",
                },
            )
    assert [e.field for e in on_profit.value.errors] == ["center"]


async def test_plan_against_what_finance_booked(as_system: Acting, call_route: CallRoute) -> None:
    async with as_system() as client:
        made = await _setup(client)
        reader = await _person(client, "budget.reader")
        bookkeeper_reads = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "finance.reader"}
        )
        # The reader also reads Finance, so the actuals show.
        group = await client.create("sys.group", {"name": f"Fin {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role",
            {"group": group["sys_id"], "role": bookkeeper_reads["records"][0]["sys_id"]},
        )
        await client.create("sys.membership", {"group": group["sys_id"], "person": reader})
        version = await client.create("budget.version", {"name": "Plan 2026", "year": 2026})
        await client.action(
            "budget.version",
            version["sys_id"],
            "budget.spread",
            {
                "center": made["Heating"]["code"],
                "cost_type": made["energy"]["code"],
                "amount": "4000",
            },
        )
        await client.create(
            "finance.booking",
            {
                "date": "2026-02-12",
                "text": "Oil",
                "cost_type": made["energy"]["sys_id"],
                "center": made["Heating"]["sys_id"],
                "amount": "3000",
            },
        )
    chart = await call_route(
        reader, "GET", "/budget/widget/plan_vs_actual", query={"version": version["sys_id"]}
    )
    figure = await call_route(
        reader, "GET", "/budget/widget/used", query={"version": version["sys_id"]}
    )
    assert chart["points"] == [
        {"key": made["Heating"]["sys_id"], "label": "Heating", "value": 75.0}
    ]
    assert (figure["value"], figure["unit"]) == (75.0, "% used")
