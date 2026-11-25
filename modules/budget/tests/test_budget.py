"""Budget on its own installation (with Costing, and Finance for the actuals): versions and their
flow, lines with a rhythm filling their months, only in drafts, moving the old lines, plan against
actual."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from p42cloud_budget import move_lines

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


async def _line(client: Client, version: str, made: dict[str, Any], **values: Any) -> Any:
    return await client.create(
        "budget.plan_line",
        {
            "version": version,
            "center": made["Heating"]["sys_id"],
            "cost_type": made["energy"]["sys_id"],
        }
        | values,
    )


async def _months(client: Client, line: Any) -> dict[int, str]:
    found = await client.list(
        "budget.month", where={"field": "line", "op": "eq", "value": line["sys_id"]}, limit=20
    )
    return {int(r["month"]): r["amount"] for r in found["records"]}


async def test_a_version_is_approved_by_a_controller_then_locked(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        made = await _setup(client)
        planner = await _person(client, "budget.planner")
        controller = await _person(client, "budget.controller")
    async with as_person(planner) as client:
        version = await client.create("budget.version", {"name": "Budget 2027", "year": 2027})
        assert version["state"] == "draft"
        await _line(client, version["sys_id"], made, rhythm="monthly", amount="10", from_month=1)
        await client.action("budget.version", version["sys_id"], "budget.move_submit")
        current = await client.get("budget.version", version["sys_id"])
        assert current["state"] == "draft"  # waiting for a controller's yes
        with pytest.raises(Problem):  # approving is a controller's, never around the approval
            await client.update(
                "budget.version", version["sys_id"], {"state": "approved"}, current["_etag"]
            )
    with pytest.raises(Problem):  # its lines don't change while it waits
        async with as_person(planner) as client:
            await _line(client, version["sys_id"], made, rhythm="once", amount="5", from_month=3)
    async with as_person(controller) as client:
        waiting = (await client.approvals("budget.version", version["sys_id"]))[0]
        assert waiting["may_decide"]
        await client.decide(waiting["sys_id"], "approve")
        assert (await client.get("budget.version", version["sys_id"]))["state"] == "approved"
        await client.action("budget.version", version["sys_id"], "budget.move_lock")
        assert (await client.get("budget.version", version["sys_id"]))["state"] == "locked"


@pytest.mark.parametrize(
    ("rhythm", "amount", "start", "months", "total"),
    [
        ("monthly", "30", 4, dict.fromkeys(range(4, 13), "30.00"), "270.00"),
        ("quarterly", "500", 2, dict.fromkeys((2, 5, 8, 11), "500.00"), "2000.00"),
        ("yearly", "1000", 1, dict.fromkeys(range(1, 12), "83.33") | {12: "83.37"}, "1000.00"),
        ("once", "750", 9, {9: "750.00"}, "750.00"),
    ],
)
async def test_a_rhythm_fills_the_months(  # noqa: PLR0913, PLR0917 - the cases
    as_system: Acting, rhythm: str, amount: str, start: int, months: dict[int, str], total: str
) -> None:
    async with as_system() as client:
        made = await _setup(client)
        version = await client.create("budget.version", {"name": "Plan", "year": 2027})
        line = await _line(
            client, version["sys_id"], made, rhythm=rhythm, amount=amount, from_month=start
        )
        assert await _months(client, line) == months
    assert line["year_total"] == total


async def test_months_follow_their_line_unless_typed_by_hand(as_system: Acting) -> None:
    async with as_system() as client:
        made = await _setup(client)
        version = await client.create("budget.version", {"name": "Plan", "year": 2027})
        line = await _line(client, version["sys_id"], made, amount="100")
        # Quarterly from July: the other months go, the year total follows.
        line = await client.update(
            "budget.plan_line",
            line["sys_id"],
            {"rhythm": "quarterly", "from_month": 7},
            line["_etag"],
        )
        assert await _months(client, line) == {7: "100.00", 10: "100.00"}
        assert line["year_total"] == "200.00"
        # By hand: the months stay and are typed; the year total adds them up.
        line = await client.update(
            "budget.plan_line", line["sys_id"], {"rhythm": "by_hand"}, line["_etag"]
        )
        await client.create("budget.month", {"line": line["sys_id"], "month": 12, "amount": "50"})
        assert (await client.get("budget.plan_line", line["sys_id"]))["year_total"] == "250.00"
    with pytest.raises(Problem) as typed:  # a rhythm's months aren't typed
        async with as_system() as client:
            other = await client.create("costing.cost_type", {"code": _code("WA"), "name": "Water"})
            monthly = await _line(
                client, version["sys_id"], made, cost_type=other["sys_id"], amount="10"
            )
            await client.create(
                "budget.month", {"line": monthly["sys_id"], "month": 1, "amount": "99"}
            )
    assert [e.field for e in typed.value.errors] == ["amount"]
    with pytest.raises(Problem) as twice:  # one line per cost center and cost type
        async with as_system() as client:
            await _line(client, version["sys_id"], made, amount="1")
    assert [e.code for e in twice.value.errors] == ["not_unique"]


async def test_lines_change_only_in_drafts_and_on_cost_centers(as_system: Acting) -> None:
    async with as_system() as client:
        made = await _setup(client)
        version = await client.create("budget.version", {"name": "Plan", "year": 2027})
        await _line(client, version["sys_id"], made, amount="10")
        await client.action("budget.version", version["sys_id"], "budget.move_submit")
    with pytest.raises(Problem) as refused:
        async with as_system() as client:
            await _line(
                client, version["sys_id"], made, center=made["Garden"]["sys_id"], amount="5"
            )
    assert [e.field for e in refused.value.errors] == ["version"]
    wrong_ones: list[tuple[dict[str, Any], str]] = [
        ({"center": made["house"]["sys_id"], "amount": "5"}, "center"),
        ({"amount": "5", "from_month": 13}, "from_month"),
        ({}, "amount"),
        ({"amount": "5", "year_total": "1"}, "year_total"),
    ]
    for wrong, field in wrong_ones:
        with pytest.raises(Problem) as invalid:
            async with as_system() as client:
                draft = await client.create("budget.version", {"name": "Other", "year": 2027})
                await _line(client, draft["sys_id"], made, **wrong)
        assert [e.field for e in invalid.value.errors] == [field]


async def test_the_lines_of_0_1_are_moved(as_system: Acting, p42_installation: Any) -> None:
    """A stand-in for 0.1's table (its lines stay there, retired): equal months become a
    monthly line, the others are typed by hand."""
    async with as_system() as client:
        made = await _setup(client)
        version = await client.create("budget.version", {"name": "Old", "year": 2026})
    heating, garden = made["Heating"]["sys_id"], made["Garden"]["sys_id"]
    old = [(heating, m, "100", "Oil" if m == 1 else None) for m in range(1, 13)]
    old += [(garden, 3, "40", "Seeds"), (garden, 6, "60", "Seeds")]
    owner = create_async_engine(p42_installation.owner_database_url)
    try:
        async with owner.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE budget.line (version uuid, center uuid, cost_type uuid,"
                    " month integer, amount numeric, currency text, note text,"
                    " deleted timestamptz)"
                )
            )
            for center, month, amount, note in old:
                await conn.execute(
                    text("INSERT INTO budget.line VALUES (:v, :c, :t, :m, :a, 'CHF', :n, NULL)"),
                    {
                        "v": version["sys_id"],
                        "c": center,
                        "t": made["energy"]["sys_id"],
                        "m": month,
                        "a": amount,
                        "n": note,
                    },
                )
            await move_lines(conn)
            await conn.execute(text("DROP TABLE budget.line"))
    finally:
        await owner.dispose()
    async with as_system() as client:
        lines = await client.list(
            "budget.plan_line",
            where={"field": "version", "op": "eq", "value": version["sys_id"]},
        )
        found = {
            str(r["center"]): (r["rhythm"], r["amount"], r["year_total"], r["note"])
            for r in lines["records"]
        }
        by_hand = next(r for r in lines["records"] if str(r["center"]) == garden)
        months = await _months(client, by_hand)
    assert found == {
        heating: ("monthly", "100.00", "1200.00", "Oil"),
        garden: ("by_hand", None, "100.00", "Seeds"),
    }
    assert months == {3: "40.00", 6: "60.00"}


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
        await _line(client, version["sys_id"], made, rhythm="yearly", amount="4000")
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
