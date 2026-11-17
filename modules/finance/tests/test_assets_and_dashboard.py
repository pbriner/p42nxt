"""Assets written off over their order's life, and the dashboard's results per profit center."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import date
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

from p42cloud_finance.assets import months_in_use

Acting = Callable[..., AbstractAsyncContextManager[Client]]
CallRoute = Callable[..., Awaitable[Any]]


def _code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


async def _setup(client: Client) -> dict[str, Any]:
    made: dict[str, Any] = {}
    made["house"] = (
        await client.create(
            "costing.center",
            {"code": _code("H"), "name": f"House {uuid.uuid4().hex[:4]}", "kind": "profit"},
        )
    )["sys_id"]
    made["flat"] = (
        await client.create(
            "costing.center", {"code": _code("F"), "name": "Flat", "parent": made["house"]}
        )
    )["sys_id"]
    for name, values in (("repairs", {}), ("depreciation", {}), ("rent", {"kind": "income"})):
        made[name] = (
            await client.create(
                "costing.cost_type", {"code": _code(name[:3]), "name": name.title()} | values
            )
        )["sys_id"]
    return made


def test_a_part_year_counts_by_months_in_use() -> None:
    assert months_in_use(date(2026, 7, 15), 2025) == 0
    assert months_in_use(date(2026, 7, 15), 2026) == 6
    assert months_in_use(date(2026, 7, 15), 2027) == 12


async def test_an_asset_is_written_off_over_its_orders_life(as_system: Acting) -> None:
    async with as_system() as client:
        made = await _setup(client)
        roof = await client.create(
            "costing.order",
            {
                "code": _code("R"),
                "name": "Roof",
                "kind": "capex",
                "useful_life": 25,
                "center": made["house"],
            },
        )
        for amount in ("6800", "38500"):
            await client.create(
                "finance.booking",
                {
                    "date": "2026-05-05",
                    "text": "Roof",
                    "cost_type": made["repairs"],
                    "order": roof["sys_id"],
                    "amount": amount,
                },
            )
        asset = await client.create(
            "finance.asset",
            {
                "name": "Roof 2026",
                "order": roof["sys_id"],
                "in_use": "2026-07-01",
                "cost_type": made["depreciation"],
            },
        )
        assert (asset["value"], asset["yearly"], asset["book_value"]) == (
            "45300.00",
            "1812.00",
            "45300.00",
        )
        said = await client.action(
            "finance.asset", asset["sys_id"], "finance.write_off", {"year": 2026}
        )
        after = await client.get("finance.asset", asset["sys_id"])
        booked = await client.list(
            "finance.booking",
            where={"field": "cost_type", "op": "eq", "value": made["depreciation"]},
        )
        with pytest.raises(Problem) as twice:
            await client.action(
                "finance.asset", asset["sys_id"], "finance.write_off", {"year": 2026}
            )
        with pytest.raises(Problem) as before:
            await client.action(
                "finance.asset", asset["sys_id"], "finance.write_off", {"year": 2025}
            )
    # July to December: 6 of 12 months of 1812.
    assert "906.00 written off for 2026" in said["message"]
    assert (after["written_off"], after["book_value"]) == ("906.00", "44394.00")
    [booking] = booked["records"]
    assert (booking["date"], booking["amount"], booking["capex"]) == ("2026-12-31", "906.00", False)
    assert twice.value.code == "written_off" and before.value.code == "not_in_use"


async def test_an_asset_comes_from_an_order_adding_value(as_system: Acting) -> None:
    async with as_system() as client:
        made = await _setup(client)
        paint = await client.create(
            "costing.order", {"code": _code("P"), "name": "Paint", "center": made["flat"]}
        )
    with pytest.raises(Problem) as refused:
        async with as_system() as client:
            await client.create(
                "finance.asset",
                {
                    "name": "Paint",
                    "order": paint["sys_id"],
                    "in_use": "2026-01-01",
                    "cost_type": made["depreciation"],
                },
            )
    assert [e.field for e in refused.value.errors] == ["order"]


async def test_the_dashboard_shows_results_per_profit_center(
    as_system: Acting, call_route: CallRoute
) -> None:
    year = date.today().year
    async with as_system() as client:
        made = await _setup(client)
        for text, cost_type, amount in (("Rent", "rent", "1950"), ("Repair", "repairs", "450")):
            await client.create(
                "finance.booking",
                {
                    "date": f"{year}-01-15",
                    "text": text,
                    "cost_type": made[cost_type],
                    "center": made["flat"],
                    "amount": amount,
                },
            )
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "finance.reader"}
        )
        group = await client.create("sys.group", {"name": f"Readers {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        person = await client.create("sys.person", {"name": f"Rea {uuid.uuid4().hex[:4]}"})
        await client.create(
            "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
        )
        house = await client.get("costing.center", made["house"])
    shown = await call_route(person["sys_id"], "GET", "/finance/widget/results")
    mine = [p for p in shown["points"] if p["key"] == made["house"]]
    assert mine == [{"key": made["house"], "label": house["name"], "value": 1500.0}]
