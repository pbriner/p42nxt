"""The one way the platform calls other systems over HTTP (E-10): `safe_client()`.

Addresses often come from people (a sign-in provider's, a connection's), so the server must not
become a way into the installation's own network:

- **https only**, and **no redirects** (a redirect could lead anywhere);
- every name is **resolved first, and refused** if any of its addresses is private, loopback,
  link-local, multicast, reserved or unspecified (IPv4 and IPv6, mapped ones too);
- optionally **only some hosts** (a connection type's), never others.

A name could resolve differently a moment later; the window is short and the hosts most worth
reaching are listed per connection type, which is the stronger limit.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Iterable

import httpx

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class Refused(httpx.HTTPError):
    """A call the platform won't make (an address inside, a host not allowed, not https)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _inside(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def resolve_public(host: str) -> None:
    """Refuses a host that is, or resolves to, an address inside a network."""
    try:
        if _inside(host):
            raise Refused(f"{host} is an address inside a network.")
        return
    except ValueError:
        pass  # a name, not an address
    loop = asyncio.get_running_loop()
    try:
        found = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise Refused(f"{host} can't be found.") from error
    if not found or any(_inside(str(info[4][0])) for info in found):
        raise Refused(f"{host} leads inside a network.")


def host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """`api.example.com` allows itself; `*.example.com` allows its subdomains, not itself."""
    host = host.lower().rstrip(".")
    for given in allowed:
        pattern = given.lower().rstrip(".")
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


class _Guard(httpx.AsyncBaseTransport):
    """Checks every request before it leaves."""

    def __init__(self, inner: httpx.AsyncBaseTransport, allowed: tuple[str, ...] | None) -> None:
        self._inner = inner
        self._allowed = allowed

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme != "https":
            raise Refused("Only https.")
        host = url.host
        if self._allowed is not None and not host_allowed(host, self._allowed):
            raise Refused(f"{host} isn't one of the hosts this may reach.")
        await resolve_public(host)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def safe_client(
    *,
    allowed_hosts: Iterable[str] | None = None,
    base_url: str = "",
    timeout: httpx.Timeout = TIMEOUT,
    auth: httpx.Auth | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """A client for outside calls; `transport` is for tests (it is still guarded)."""
    guard = _Guard(
        transport or httpx.AsyncHTTPTransport(retries=0),
        tuple(allowed_hosts) if allowed_hosts is not None else None,
    )
    return httpx.AsyncClient(
        transport=guard, base_url=base_url, timeout=timeout, auth=auth, follow_redirects=False
    )
