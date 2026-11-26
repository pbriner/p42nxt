"""IPAM: subnets that don't overlap, addresses in their subnet, the next free one, DNS records
linked to their address."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from p42cloud.sdk import Client, Problem

from p42cloud_ipam import next_free

Acting = Callable[..., AbstractAsyncContextManager[Client]]


def _net() -> str:
    n = uuid.uuid4().int
    return f"10.{n % 200 + 20}.{(n >> 8) % 250}.0/24"


async def test_subnets_addresses_and_the_next_free_one(as_system: Acting) -> None:
    cidr = _net()
    base = cidr.rsplit(".", 1)[0]
    async with as_system() as client:
        subnet = await client.create(
            "ipam.subnet",
            {
                "cidr": cidr,
                "gateway": f"{base}.1",
                "dhcp_from": f"{base}.100",
                "dhcp_to": f"{base}.200",
            },
        )
        with pytest.raises(Problem):  # overlaps
            await client.create("ipam.subnet", {"cidr": f"{base}.128/25"})
        with pytest.raises(Problem):  # gateway outside
            await client.create("ipam.subnet", {"cidr": _net(), "gateway": "192.168.1.1"})
        address = await client.create("ipam.address", {"address": f"{base}.2"})
        assert str(address["subnet"]) == str(subnet["sys_id"])
        assert await next_free(client, str(subnet["sys_id"])) == f"{base}.3"
        zone = await client.create("ipam.dns_zone", {"name": f"z{uuid.uuid4().hex[:6]}.lan"})
        record = await client.create(
            "ipam.dns_record",
            {"zone": zone["sys_id"], "name": "nas", "type": "a", "value": f"{base}.2"},
        )
    assert str(record["address"]) == str(address["sys_id"])
