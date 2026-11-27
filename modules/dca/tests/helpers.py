"""What the DCA tests share: an investor with a Kraken connection, a plan, reading as them."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from p42cloud.sdk import Client

from p42cloud_dca.kraken import TYPE

Acting = Callable[..., AbstractAsyncContextManager[Client]]


async def investor(as_system: Acting) -> str:
    """Someone holding the DCA investor role, with a Kraken connection."""
    async with as_system() as client:
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "dca.user"}
        )
        group = await client.create("sys.group", {"name": f"Investors {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        person = await client.create("sys.person", {"name": f"Ivy {uuid.uuid4().hex[:4]}"})
        await client.create(
            "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
        )
    return person["sys_id"]


async def connect(as_person: Acting, person: str) -> None:
    async with as_person(person) as client:
        made = await client.create("sys.connection", {"type": TYPE, "label": "Kraken"})
        for name, value in (("api_key", "key"), ("api_secret", "c2VjcmV0")):
            await client.create(
                "sys.connection_secret",
                {"connection": made["sys_id"], "name": name, "value": value},
            )


async def pair_id(client: Client, code: str = "XBTCHF") -> str:
    """A pair's record by Kraken's name (the six coin pairs are there from the start)."""
    found = await client.list("dca.pair", where={"field": "code", "op": "eq", "value": code})
    return str(found["records"][0]["sys_id"])


async def new_plan(
    as_person: Acting, person: str, *, code: str = "XBTCHF", **values: Any
) -> dict[str, Any]:
    async with as_person(person) as client:
        base = {"name": "Bitcoin", "trading_pair": await pair_id(client, code), "amount": "0.001"}
        return await client.create("dca.plan", base | values)


async def read(as_person: Acting, person: str, table: str, **where: Any) -> list[dict[str, Any]]:
    async with as_person(person) as client:
        found = await client.list(
            table,
            where={"and": [{"field": k, "op": "eq", "value": v} for k, v in where.items()]}
            if where
            else None,
        )
    return found["records"]
