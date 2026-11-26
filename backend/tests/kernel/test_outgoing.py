"""The local exception of the safe client: only the ranges named, by address, http or https."""

import httpx
import pytest

from p42cloud.kernel.outgoing import Refused, safe_client, within


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"host": request.url.host})


def test_addresses_are_within_ranges_or_not() -> None:
    assert within("192.168.1.50", ["192.168.1.0/24"])
    assert not within("192.168.2.50", ["192.168.1.0/24"])
    assert not within("shelly.local", ["192.168.1.0/24"])  # names aren't, only addresses


async def test_a_local_client_reaches_only_its_ranges() -> None:
    client = safe_client(local_ranges=["192.168.1.0/24"], transport=httpx.MockTransport(_ok))
    async with client:
        answer = await client.get("http://192.168.1.50/rpc/Shelly.GetStatus")
        assert answer.json() == {"host": "192.168.1.50"}
        for elsewhere in ("http://192.168.2.1/", "http://10.0.0.1/", "https://example.com/"):
            with pytest.raises(Refused):
                await client.get(elsewhere)


async def test_without_ranges_inside_stays_refused() -> None:
    client = safe_client(transport=httpx.MockTransport(_ok))
    async with client:
        with pytest.raises(Refused):
            await client.get("http://192.168.1.50/")
