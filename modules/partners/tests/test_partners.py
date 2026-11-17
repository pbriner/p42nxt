"""Partners on their own installation: IBANs, contracts and their flow, who may change them."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

from p42cloud_partners import iban_ok

Acting = Callable[..., AbstractAsyncContextManager[Client]]


async def _refused(as_system: Acting, table: str, values: dict[str, Any]) -> list[str]:
    with pytest.raises(Problem) as refused:
        async with as_system() as client:
            await client.create(table, values)
    return [e.field for e in refused.value.errors]


@pytest.mark.parametrize(
    ("iban", "ok"),
    [
        ("CH93 0076 2011 6238 5295 7", True),
        ("ch9300762011623852957", True),
        ("CH93 0076 2011 6238 5295 8", False),
        ("DE89 3704 0044 0532 0130 00", True),
        ("not an iban", False),
    ],
)
def test_an_iban_is_checked_by_its_digits(iban: str, ok: bool) -> None:
    assert iban_ok(iban) is ok


async def test_a_partner_keeps_its_iban_in_groups_of_four(as_system: Acting) -> None:
    async with as_system() as client:
        made = await client.create(
            "partners.partner",
            {
                "name": "Roofer AG",
                "supplier": True,
                "iban": "ch9300762011623852957",
                "country": "ch",
            },
        )
    assert made["iban"] == "CH93 0076 2011 6238 5295 7"
    assert made["country"] == "CH"
    wrong = {"name": "X", "iban": "CH93 0076 2011 6238 5295 8"}
    assert await _refused(as_system, "partners.partner", wrong) == ["iban"]


async def test_a_lease_goes_from_draft_into_force_and_ends(as_system: Acting) -> None:
    async with as_system() as client:
        tenant = await client.create(
            "partners.partner", {"name": "Anna Muster", "kind": "person", "tenant": True}
        )
        lease = await client.create(
            "partners.contract",
            {
                "partner": tenant["sys_id"],
                "name": "Lease ground floor",
                "kind": "lease",
                "start": "2026-01-01",
                "amount": "1950",
                "advance": "180",
            },
        )
        assert (lease["state"], lease["currency"], lease["amount"], lease["advance"]) == (
            "draft",
            "CHF",
            "1950.00",
            "180.00",
        )
        await client.action("partners.contract", lease["sys_id"], "partners.move_activate")
        assert (await client.get("partners.contract", lease["sys_id"]))["state"] == "active"
    backwards = {
        "partner": tenant["sys_id"],
        "name": "X",
        "start": "2026-05-01",
        "end": "2026-04-01",
    }
    assert await _refused(as_system, "partners.contract", backwards) == ["end"]


async def test_readers_read_and_only_managers_change(as_system: Acting, as_person: Acting) -> None:
    async with as_system() as client:
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "partners.reader"}
        )
        group = await client.create("sys.group", {"name": f"Readers {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        person = await client.create("sys.person", {"name": f"Rea {uuid.uuid4().hex[:4]}"})
        await client.create(
            "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
        )
        partner = await client.create("partners.partner", {"name": "Gardener GmbH"})
    async with as_person(person["sys_id"]) as client:
        seen = await client.get("partners.partner", partner["sys_id"])
        with pytest.raises(Problem) as refused:
            await client.create("partners.partner", {"name": "Z"})
    assert seen["name"] == "Gardener GmbH"
    assert refused.value.status == 403
