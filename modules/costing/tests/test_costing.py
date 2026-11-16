"""Costing on its own installation: the tree of centers and their profit centers, orders, keys,
and who may change the structure."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

Acting = Callable[..., AbstractAsyncContextManager[Client]]


def _code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


async def _center(client: Client, name: str, kind: str = "cost", **values: Any) -> dict[str, Any]:
    return await client.create(
        "costing.center", {"code": _code(name[:4]), "name": name, "kind": kind} | values
    )


async def _get(as_system: Acting, table: str, sys_id: str) -> dict[str, Any]:
    async with as_system() as client:
        return await client.get(table, sys_id)


async def _refused(as_system: Acting, table: str, values: dict[str, Any]) -> list[str]:
    with pytest.raises(Problem) as refused:
        async with as_system() as client:
            await client.create(table, values)
    return [e.field for e in refused.value.errors]


async def test_a_center_knows_the_profit_center_it_counts_for(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _center(client, "Erlenweg 12", "profit")
        floor = await _center(client, "Ground floor", parent=house["sys_id"])
        heating = await _center(client, "Heating", parent=floor["sys_id"], shared=True)
        other = await _center(client, "Seeblick 4", "profit")
    assert (await _get(as_system, "costing.center", house["sys_id"]))["profit_center"] == house[
        "sys_id"
    ]
    assert floor["profit_center"] == house["sys_id"]
    assert heating["profit_center"] == house["sys_id"]

    # Moved under another house: everything under it follows.
    async with as_system() as client:
        current = await client.get("costing.center", floor["sys_id"])
        await client.update(
            "costing.center", floor["sys_id"], {"parent": other["sys_id"]}, current["_etag"]
        )
    assert (await _get(as_system, "costing.center", heating["sys_id"]))["profit_center"] == other[
        "sys_id"
    ]


async def test_profit_centers_sit_at_the_top_or_under_profit_centers(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _center(client, "House", "profit")
        floor = await _center(client, "Floor", parent=house["sys_id"])
        division = await _center(client, "Division", "profit")
        await _center(client, "Unit", "profit", parent=division["sys_id"])  # fine
    under_cost = {"code": _code("x"), "name": "X", "kind": "profit", "parent": floor["sys_id"]}
    assert await _refused(as_system, "costing.center", under_cost) == ["parent"]
    shared = {"code": _code("y"), "name": "Y", "kind": "profit", "shared": True}
    assert await _refused(as_system, "costing.center", shared) == ["shared"]


async def test_an_order_adding_value_is_written_off_over_years(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _center(client, "House", "profit")
        roof = await client.create(
            "costing.order",
            {
                "code": _code("roof"),
                "name": "Roof 2026",
                "kind": "capex",
                "center": house["sys_id"],
                "useful_life": 25,
                "start": "2026-04-01",
                "end": "2026-06-30",
            },
        )
    assert roof["state"] == "open"
    base = {"code": _code("o"), "name": "O", "center": house["sys_id"]}
    assert await _refused(as_system, "costing.order", base | {"kind": "capex"}) == ["useful_life"]
    backwards = base | {"start": "2026-05-01", "end": "2026-04-01"}
    assert await _refused(as_system, "costing.order", backwards) == ["end"]


async def test_a_key_spreads_a_shared_center_over_cost_centers(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _center(client, "House", "profit")
        ground = await _center(client, "Ground", parent=house["sys_id"])
        upper = await _center(client, "Upper", parent=house["sys_id"])
        heating = await _center(client, "Heating", parent=house["sys_id"], shared=True)
        stairs = await _center(client, "Stairs", parent=house["sys_id"])  # not shared
        key = await client.create(
            "costing.key", {"name": "Heating by m²", "center": heating["sys_id"], "unit": "m2"}
        )
        for center, value in ((ground, "95"), (upper, "110")):
            await client.create(
                "costing.key_share",
                {"key": key["sys_id"], "center": center["sys_id"], "value": value},
            )
        shares = await client.list(
            "costing.key_share", where={"field": "key", "op": "eq", "value": key["sys_id"]}
        )
    assert sorted(s["value"] for s in shares["records"]) == ["110.0000", "95.0000"]
    not_shared = {"name": "Stairs", "center": stairs["sys_id"]}
    assert await _refused(as_system, "costing.key", not_shared) == ["center"]
    share = {"key": key["sys_id"], "value": "1"}
    assert await _refused(
        as_system, "costing.key_share", share | {"center": heating["sys_id"]}
    ) == ["center"]
    assert await _refused(as_system, "costing.key_share", share | {"center": house["sys_id"]}) == [
        "center"
    ]
    negative = {"key": key["sys_id"], "center": stairs["sys_id"], "value": "-5"}
    assert await _refused(as_system, "costing.key_share", negative) == ["value"]


async def test_within_finds_everything_under_a_center(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _center(client, "House", "profit")
        floor = await _center(client, "Floor", parent=house["sys_id"])
        await _center(client, "Boiler", parent=floor["sys_id"])
        found = await client.list(
            "costing.center", where={"field": "sys_id", "op": "within", "value": house["sys_id"]}
        )
    assert sorted(r["name"] for r in found["records"]) == ["Boiler", "Floor", "House"]


async def test_readers_read_and_only_controllers_change(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "costing.reader"}
        )
        group = await client.create("sys.group", {"name": f"Readers {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        person = await client.create("sys.person", {"name": f"Rea {uuid.uuid4().hex[:4]}"})
        await client.create(
            "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
        )
        house = await _center(client, "House", "profit")
    async with as_person(person["sys_id"]) as client:
        seen = await client.get("costing.center", house["sys_id"])
        with pytest.raises(Problem) as refused:
            await client.create("costing.center", {"code": _code("z"), "name": "Z"})
    assert seen["name"] == "House"
    assert refused.value.status == 403


async def test_an_order_is_done_then_settled_by_a_controller(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "costing.controller"}
        )
        group = await client.create("sys.group", {"name": f"Ctl {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        person = await client.create("sys.person", {"name": f"Cora {uuid.uuid4().hex[:4]}"})
        await client.create(
            "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
        )
    async with as_person(person["sys_id"]) as client:
        house = await _center(client, "House", "profit")
        order = await client.create(
            "costing.order", {"code": _code("o"), "name": "Paint", "center": house["sys_id"]}
        )
        assert order["state"] == "open"
        with pytest.raises(Problem) as skipped:
            await client.update(
                "costing.order", order["sys_id"], {"state": "settled"}, order["_etag"]
            )
        assert skipped.value.errors[0].message == "From Open it can't go to Settled."
        done = await client.action("costing.order", order["sys_id"], "costing.move_finish")
        assert done is not None
        current = await client.get("costing.order", order["sys_id"])
        assert current["state"] == "done"
        await client.update(
            "costing.order", order["sys_id"], {"state": "settled"}, current["_etag"]
        )
        assert (await client.get("costing.order", order["sys_id"]))["state"] == "settled"
