"""Places as one tree: sensible nesting, racks with their units, paths."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from p42cloud.sdk import Client, Problem

Acting = Callable[..., AbstractAsyncContextManager[Client]]


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def test_places_nest_sensibly_and_racks_have_units(as_system: Acting) -> None:
    async with as_system() as client:
        site = await client.create("places.place", {"name": f"HQ {_tag()}", "kind": "site"})
        building = await client.create(
            "places.place", {"name": "A", "kind": "building", "parent": site["sys_id"]}
        )
        room = await client.create(
            "places.place", {"name": "Server room", "kind": "room", "parent": building["sys_id"]}
        )
        rack = await client.create(
            "places.place", {"name": "R1", "kind": "rack", "parent": room["sys_id"]}
        )
        assert rack["units"] == 42
        for wrong, field in (
            ({"name": "B", "kind": "building", "parent": room["sys_id"]}, "parent"),
            ({"name": "R2", "kind": "rack", "parent": building["sys_id"]}, "parent"),
            ({"name": "Big", "kind": "rack", "parent": room["sys_id"], "units": 99}, "units"),
            ({"name": "Hall", "kind": "room", "parent": room["sys_id"], "units": 4}, "parent"),
        ):
            with pytest.raises(Problem) as refused:
                await client.create("places.place", wrong)
            assert refused.value.errors[0].field == field, wrong
        inside = await client.list(
            "places.place",
            where={"field": "sys_id", "op": "within", "value": [site["sys_id"]]},
        )
    assert {r["name"] for r in inside["records"]} >= {"A", "Server room", "R1"}
