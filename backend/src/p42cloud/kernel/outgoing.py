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
  By address, or by a name that is looked up on each request: every address it has must be in
  the ranges, and the request goes to the address just checked (so the name can't point
  elsewhere in between), its certificate checked against the name.

A name could resolve differently a moment later; the window is short and the hosts most worth
reaching are listed per connection type, which is the stronger limit.
"""

import asyncio
import ipaddress
import re
import socket
import ssl
from collections.abc import Iterable
from typing import Literal

import httpx

from p42cloud.kernel.config import Settings, get_settings

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class Refused(httpx.HTTPError):
    """A call the platform won't make (an address inside, a host not allowed, not https)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class Held(Refused):
    """A change in an outside system that a test installation doesn't make (silent mode)."""


HELD = "Held: this is a test installation, so nothing that changes things outside is sent."
READING = frozenset({"GET", "HEAD", "OPTIONS"})
# What a client holds on a test installation: nothing (the platform's own reading, sign-in),
# changes (anything but reading: a connection's default), or everything (a module said so).
type Holds = Literal["nothing", "changes", "everything"]

# What this process was started with (production, the environment's delivery) and the setting
# "Deliver outside" as it last read it.
_READ: dict[str, bool] = {}


def configure(settings: Settings) -> None:
    """The process's own settings, given when it starts (the app, the worker, tests)."""
    _READ["production"] = settings.environment == "production"
    _READ["started"] = settings.deliver_outside


def set_delivery(on: bool | None) -> None:
    """The setting's value, given by whoever reads settings (every 30 seconds); None: unread,
    the environment's counts."""
    if on is None:
        _READ.pop("deliver", None)
    else:
        _READ["deliver"] = on


def outside_allowed() -> bool:
    """Whether messages and changes reach the outside: always in production; elsewhere only
    when an administrator turned delivery on. Unknown: held (fail closed)."""
    if "production" not in _READ:
        try:
            configure(get_settings())
        except ValueError:
            return False
    if _READ["production"]:
        return True
    return _READ.get("deliver", _READ["started"])


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


# Never reached as a local device, whatever ranges are named: this machine, its own network
# links and the cloud's metadata address, and IPv6 prefixes that wrap IPv4 addresses.
_WRAPPING = (ipaddress.ip_network("::ffff:0:0/96"), ipaddress.ip_network("64:ff9b::/96"))
_NEVER_V4 = (ipaddress.ip_network("0.0.0.0/8"),)


def never_local(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> bool:
    """Whether a range (or one address as /32, /128) touches what no local connection reaches."""
    if isinstance(net, ipaddress.IPv6Network) and any(net.overlaps(w) for w in _WRAPPING):
        return True
    return (
        not net.is_private
        or net.is_loopback
        or net.is_link_local
        or net.is_multicast
        or net.is_unspecified
        or net.is_reserved
        or (isinstance(net, ipaddress.IPv4Network) and any(net.overlaps(n) for n in _NEVER_V4))
    )


_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_NAME = re.compile(rf"^{_LABEL}(\.{_LABEL})*$")


def is_name(host: str) -> bool:
    """A host name (not an address): letters, digits, hyphens and dots, at most 253 long."""
    host = host.lower().rstrip(".")
    if len(host) > 253 or not _NAME.match(host):  # noqa: PLR2004 - DNS's limit
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


async def lookup(host: str) -> list[str]:
    """The addresses a name has now (tests replace this)."""
    loop = asyncio.get_running_loop()
    found = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in found))


async def local_address(host: str, ranges: Iterable[str]) -> str:
    """The address to reach a local device at: itself if it's an address in the ranges; for a
    name, its first address, after every address it has was found in the ranges."""
    ranges = tuple(ranges)
    if not is_name(host):
        if within(host, ranges):
            return host.strip("[]")
        raise Refused(f"{host} isn't in the local ranges this connection may reach.")
    try:
        found = await lookup(host)
    except socket.gaierror as error:
        raise Refused(f"{host} can't be found.") from error
    if not found:
        raise Refused(f"{host} can't be found.")
    outside = [a for a in found if not within(a, ranges)]
    if outside:
        raise Refused(
            f"{host} leads to {outside[0]}, which isn't in the local ranges this connection"
            " may reach."
        )
    return found[0]


def pinned_context(pem: str) -> ssl.SSLContext:
    """Trusts exactly one certificate (a device's own), whatever name it's reached by.

    Devices make their own certificates loosely: a Dream Machine's may sign certificates without
    saying it's an authority. Python's strict check refuses such a certificate as the one to
    trust, so it's off here; still nothing but this certificate is trusted, and only its
    private key's holder can answer with it. Raises ssl.SSLError if it isn't a certificate."""
    context = ssl.create_default_context(cadata=pem)
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    context.check_hostname = False
    return context


def within(host: str, ranges: Iterable[str]) -> bool:
    """Whether an address (not a name) is in one of the ranges, and one a device may have."""
    try:
        ip = ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    if never_local(ipaddress.ip_network(ip)):
        return False
    return any(ip in ipaddress.ip_network(r, strict=False) for r in ranges)


class _Guard(httpx.AsyncBaseTransport):
    """Checks every request before it leaves."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        allowed: tuple[str, ...] | None,
        local: tuple[str, ...] | None = None,
        holds: Holds = "nothing",
    ) -> None:
        self._inner = inner
        self._allowed = allowed
        self._local = local
        self._holds = holds

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        acts = self._holds == "everything" or (
            self._holds == "changes" and request.method not in READING
        )
        if acts and not outside_allowed():
            raise Held(HELD)
        if self._local is not None:
            # Only the ranges named on the connection, by address, and nothing outside them.
            if url.scheme not in ("http", "https"):
                raise Refused("Only http or https.")
            address = await local_address(url.host, self._local)
            if address != url.host.strip("[]"):
                # To the address just checked; the Host header keeps the name, and TLS checks
                # the certificate against it.
                request.extensions = {**request.extensions, "sni_hostname": url.host}
                request.url = url.copy_with(host=address)
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
    holds: Holds = "nothing",
) -> httpx.AsyncClient:
    """A client for outside calls; `transport` is for tests (it is still guarded).
    `local_ranges`: a local connection's ranges, the only addresses it then reaches; `trusted`:
    the one certificate (PEM) such a device has made itself, trusted instead of any authority;
    `holds`: what a test installation keeps from being sent (`Held`)."""
    if transport is None and trusted:
        transport = httpx.AsyncHTTPTransport(retries=0, verify=pinned_context(trusted))
    guard = _Guard(
        transport or httpx.AsyncHTTPTransport(retries=0),
        tuple(allowed_hosts) if allowed_hosts is not None else None,
        tuple(local_ranges) if local_ranges is not None else None,
        holds,
    )
    return httpx.AsyncClient(
        transport=guard, base_url=base_url, timeout=timeout, auth=auth, follow_redirects=False
    )
