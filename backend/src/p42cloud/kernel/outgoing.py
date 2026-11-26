"""The one way the platform calls other systems over HTTP (E-10): `safe_client()`.

Addresses often come from people (a sign-in provider's, a connection's), so the server must not
become a way into the installation's own network:

- **https only**, and **no redirects** (a redirect could lead anywhere);
- every name is **resolved first, and refused** if any of its addresses is private, loopback,
  link-local, multicast, reserved or unspecified (IPv4 and IPv6, mapped ones too);
- optionally **only some hosts** (a connection type's), never others;
- the one exception, explicit and per connection: a connection of a **local** type reaches only
  the address ranges inside its network that an administrator named on it (`local_ranges`),
  over http or https (devices at home: smart plugs, a router, a hypervisor), and nothing else.

A name could resolve differently a moment later; the window is short and the hosts most worth
reaching are listed per connection type, which is the stronger limit.
"""

import asyncio
import ipaddress
import socket
import ssl
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


def within(host: str, ranges: Iterable[str]) -> bool:
    """Whether an address (not a name) is in one of the ranges."""
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(r, strict=False) for r in ranges)


class _Guard(httpx.AsyncBaseTransport):
    """Checks every request before it leaves."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        allowed: tuple[str, ...] | None,
        local: tuple[str, ...] | None = None,
    ) -> None:
        self._inner = inner
        self._allowed = allowed
        self._local = local

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if self._local is not None:
            # Only the ranges named on the connection, by address, and nothing outside them.
            if url.scheme not in ("http", "https"):
                raise Refused("Only http or https.")
            if not within(url.host, self._local):
                raise Refused(f"{url.host} isn't in the local ranges this connection may reach.")
            return await self._inner.handle_async_request(request)
        if url.scheme != "https":
            raise Refused("Only https.")
        host = url.host
        if self._allowed is not None and not host_allowed(host, self._allowed):
            raise Refused(f"{host} isn't one of the hosts this may reach.")
        await resolve_public(host)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def safe_client(  # noqa: PLR0913 - the client's options, all by keyword
    *,
    allowed_hosts: Iterable[str] | None = None,
    base_url: str = "",
    timeout: httpx.Timeout = TIMEOUT,
    auth: httpx.Auth | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    local_ranges: Iterable[str] | None = None,
    trusted: str | None = None,
) -> httpx.AsyncClient:
    """A client for outside calls; `transport` is for tests (it is still guarded).
    `local_ranges`: a local connection's ranges, the only addresses it then reaches; `trusted`:
    the one certificate (PEM) such a device has made itself, trusted instead of any authority."""
    if transport is None and trusted:
        context = ssl.create_default_context(cadata=trusted)
        context.check_hostname = False  # it's reached by its address, its certificate pinned
        transport = httpx.AsyncHTTPTransport(retries=0, verify=context)
    guard = _Guard(
        transport or httpx.AsyncHTTPTransport(retries=0),
        tuple(allowed_hosts) if allowed_hosts is not None else None,
        tuple(local_ranges) if local_ranges is not None else None,
    )
    return httpx.AsyncClient(
        transport=guard, base_url=base_url, timeout=timeout, auth=auth, follow_redirects=False
    )
