"""Finance on its own installation (with Costing and Partners): bookings and what follows from
them, and a running-cost statement for a tenant."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

Acting = Callable[..., AbstractAsyncContextManager[Client]]


def _code(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


async def _house(client: Client) -> dict[str, Any]:
    """A rented house: two flats, shared heating spread by m² (95 and 110), cost types."""
    made: dict[str, Any] = {}

    async def center(name: str, **values: Any) -> str:
        record = await client.create(
            "costing.center", {"code": _code(name[:3]), "name": name} | values
        )
        return str(record["sys_id"])

    made["house"] = await center("House", kind="profit")
    made["ground"] = await center("Ground floor", parent=made["house"])
    made["upper"] = await center("Upper floor", parent=made["house"])
    made["heating"] = await center("Heating", parent=made["house"], shared=True)
    key = await client.create(
        "costing.key", {"name": "Heating by m²", "center": made["heating"], "unit": "m2"}
    )
    for flat, value in (("ground", "95"), ("upper", "110")):
        await client.create(
            "costing.key_share", {"key": key["sys_id"], "center": made[flat], "value": value}
        )
    for name, values in (
        ("energy", {"chargeable": True}),
        ("water", {"chargeable": True}),
        ("insurance", {}),
        ("rent", {"kind": "income"}),
    ):
        record = await client.create(
            "costing.cost_type", {"code": _code(name[:3]), "name": name.title()} | values
        )
        made[name] = record["sys_id"]
    return made


async def _book(client: Client, **values: Any) -> dict[str, Any]:
    base = {"date": "2026-02-12", "text": "Booking"}  # the currency as it defaults: CHF
    return await client.create("finance.booking", base | values)


async def test_a_booking_works_out_what_follows_from_its_answers(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _house(client)
        oil = await _book(
            client, text="Oil", cost_type=house["energy"], center=house["heating"], amount="3420"
        )
        rent = await _book(
            client, text="Rent", cost_type=house["rent"], center=house["ground"], amount="1950"
        )
        roof = await client.create(
            "costing.order",
            {
                "code": _code("roof"),
                "name": "Roof",
                "kind": "capex",
                "useful_life": 25,
                "center": house["house"],
            },
        )
        tiles = await _book(
            client, text="Tiles", cost_type=house["insurance"], order=roof["sys_id"], amount="38500"
        )
        euro = await _book(
            client,
            text="Paint",
            cost_type=house["insurance"],
            center=house["upper"],
            currency="EUR",
            amount="100",
        )
    assert (oil["profit_center"], oil["kind"], oil["capex"]) == (house["house"], "cost", False)
    assert (oil["main_currency"], oil["amount_main"]) == ("CHF", "3420.00")
    assert rent["kind"] == "income"
    assert (tiles["center"], tiles["capex"]) == (house["house"], True)  # the order's center
    assert euro["amount_main"] is None  # no rate for EUR yet: left open, not guessed


async def test_bookings_are_checked(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _house(client)
        order = await client.create(
            "costing.order", {"code": _code("o"), "name": "Paint", "center": house["upper"]}
        )
        for state in ("done", "settled"):
            current = await client.get("costing.order", order["sys_id"])
            await client.update(
                "costing.order", order["sys_id"], {"state": state}, current["_etag"]
            )

    async def refused(**values: Any) -> list[str]:
        with pytest.raises(Problem) as found:
            async with as_system() as client:
                await _book(client, cost_type=house["energy"], **values)
        return [e.field for e in found.value.errors]

    assert await refused(amount="10") == ["center"]
    assert await refused(amount="0", center=house["ground"]) == ["amount"]
    assert await refused(amount="10", order=order["sys_id"]) == ["order"]  # settled


async def test_a_tenant_gets_a_running_cost_statement(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _house(client)
        tenant = await client.create(
            "partners.partner", {"name": "Anna", "kind": "person", "tenant": True}
        )
        lease = await client.create(
            "partners.contract",
            {
                "partner": tenant["sys_id"],
                "name": "Lease ground floor",
                "kind": "lease",
                "amount": "1950",
                "advance": "180",
            },
        )
        rental = await client.create(
            "finance.rental", {"contract": lease["sys_id"], "center": house["ground"]}
        )
        await _book(
            client, text="Oil", cost_type=house["energy"], center=house["heating"], amount="3420"
        )
        await _book(
            client, text="Water", cost_type=house["water"], center=house["ground"], amount="100"
        )
        await _book(
            client,
            text="Insurance",
            cost_type=house["insurance"],
            center=house["ground"],
            amount="1280",
        )
        await _book(
            client, text="Upper water", cost_type=house["water"], center=house["upper"], amount="80"
        )
        await _book(
            client,
            text="Old oil",
            cost_type=house["energy"],
            center=house["heating"],
            amount="999",
            date="2025-12-01",
        )
        statement = await client.create(
            "finance.statement",
            {"rental": rental["sys_id"], "start": "2026-01-01", "end": "2026-05-31"},
        )
        said = await client.action("finance.statement", statement["sys_id"], "finance.work_out")
        done = await client.get("finance.statement", statement["sys_id"])
        lines = await client.list(
            "finance.statement_line",
            where={"field": "statement", "op": "eq", "value": statement["sys_id"]},
        )
        # Worked out twice: the lines are replaced, not doubled.
        await client.action("finance.statement", statement["sys_id"], "finance.work_out")
        again = await client.list(
            "finance.statement_line",
            where={"field": "statement", "op": "eq", "value": statement["sys_id"]},
        )
    # Oil: 3420 x 95/205 = 1584.88; the ground floor's water 100; insurance isn't chargeable.
    assert sorted((line["text"], line["amount"]) for line in lines["records"]) == [
        ("Advances paid: 5 x 180.00", "900.00"),
        ("Energy", "1584.88"),
        ("Water", "100.00"),
    ]
    assert (done["costs"], done["advances"], done["due"]) == ("1684.88", "900.00", "784.88")
    assert "784.88 to pay" in said["message"]
    assert len(again["records"]) == 3


async def test_a_rental_is_a_lease_for_a_flat(as_system: Acting) -> None:
    async with as_system() as client:
        house = await _house(client)
        partner = await client.create("partners.partner", {"name": "Insurer"})
        policy = await client.create(
            "partners.contract",
            {"partner": partner["sys_id"], "name": "Policy", "kind": "insurance"},
        )
        lease = await client.create(
            "partners.contract", {"partner": partner["sys_id"], "name": "Lease", "kind": "lease"}
        )
    for values, field in (
        ({"contract": policy["sys_id"], "center": house["ground"]}, "contract"),
        ({"contract": lease["sys_id"], "center": house["house"]}, "center"),
    ):
        with pytest.raises(Problem) as refused:
            async with as_system() as client:
                await client.create("finance.rental", values)
        assert [e.field for e in refused.value.errors] == [field]
