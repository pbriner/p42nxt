"""IPAM: VLANs, subnets, IP addresses (with the next free one) and DNS zones and records. Zones
are kept by hand, read from a UniFi console, or read from any DNS server by zone transfer
(AXFR); records pointing to an address link to the CMDB item using it.
"""

import asyncio
import ipaddress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import dns.exception
import dns.query
import dns.rdatatype
import dns.zone
from p42cloud.sdk import (
    ActionResult,
    ActionRun,
    Change,
    Choice,
    Client,
    ConnectionType,
    DateTime,
    FieldError,
    FormSection,
    FormView,
    Grant,
    Integer,
    Item,
    Job,
    JobRun,
    ListView,
    Module,
    Problem,
    Reference,
    RelatedList,
    Role,
    Route,
    RouteRequest,
    Schedule,
    Section,
    String,
    Table,
    TableHook,
    Text,
    UIAction,
    Widget,
    package_version,
)

ALL = ("read", "create", "write", "delete", "restore")
RECORD_TYPES = {
    t.lower(): t for t in ("A", "AAAA", "CNAME", "MX", "TXT", "SRV", "CAA", "NS", "PTR")
}
SOURCES = {"manual": "By hand", "unifi": "UniFi", "axfr": "Zone transfer"}
MAX_SCAN = 4096  # addresses looked through for the next free one

VLAN = Table(
    "vlan",
    "VLAN",
    {
        "number": Integer("Number", required=True),
        "name": String("Name", max_length=80, required=True),
        "site": Reference("Site", "places.place"),
        "notes": Text("Notes"),
    },
    label_plural="VLANs",
    display="name",
    unique_together=(("number", "site"),),
)

SUBNET = Table(
    "subnet",
    "Subnet",
    {
        "cidr": String("Network (CIDR)", max_length=64, required=True, unique=True),
        "name": String("Name", max_length=80),
        "vlan": Reference("VLAN", "ipam.vlan"),
        "place": Reference("Building or site", "places.place"),
        "gateway": String("Gateway", max_length=64),
        "dhcp_from": String("DHCP from", max_length=64),
        "dhcp_to": String("DHCP to", max_length=64),
        "notes": Text("Notes"),
    },
    label_plural="Subnets",
    display="cidr",
)

ADDRESS = Table(
    "address",
    "IP address",
    {
        "address": String("Address", max_length=64, required=True, unique=True),
        "subnet": Reference("Subnet", "ipam.subnet", read_only=True, on_delete="restrict"),
        "kind": Choice(
            "Kind",
            {"static": "Static", "dhcp": "DHCP", "reserved": "Reserved", "gateway": "Gateway"},
            required=True,
            default="static",
        ),
        # The CMDB item using it, by its index ID (the CMDB is optional).
        "ci": String("Used by (CMDB item)", max_length=36),
        "used_by": String("Used by", max_length=160),
        "dns_name": String("DNS name", max_length=253),
        "last_seen": DateTime("Last seen", read_only=True),
        "notes": Text("Notes"),
    },
    label_plural="IP addresses",
    display="address",
)

ZONE = Table(
    "dns_zone",
    "DNS zone",
    {
        "name": String("Zone", max_length=253, required=True, unique=True),
        "kept_by": Choice("Kept", SOURCES, required=True, default="manual"),
        # Zone transfer: the DNS server's address, in your network (servers that ask for a key
        # aren't read yet).
        "server": String("DNS server", max_length=64),
        "serial": String("Serial", max_length=20, read_only=True),
        "read": DateTime("Last read", read_only=True),
        "notes": Text("Notes"),
    },
    label_plural="DNS zones",
    display="name",
)

DNS_RECORD = Table(
    "dns_record",
    "DNS record",
    {
        "zone": Reference("Zone", "ipam.dns_zone", required=True, on_delete="cascade"),
        "name": String("Name", max_length=253, required=True),
        "type": Choice("Type", RECORD_TYPES, required=True, default="a"),
        "value": String("Value", max_length=1024, required=True),
        "ttl": Integer("TTL (seconds)"),
        "priority": Integer("Priority"),
        "address": Reference("Address", "ipam.address", read_only=True),
        "origin": Choice("Source", SOURCES, required=True, default="manual"),
    },
    label_plural="DNS records",
    display="name",
)

TABLES = (VLAN, SUBNET, ADDRESS, ZONE, DNS_RECORD)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _every(client: Client, table: str, where: Any = None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await client.list(table, where=where, limit=500, after=after)
        found += page["records"]
        after = page["next"]
        if after is None:
            return found


async def _check_subnet(change: Change) -> None:
    """A real network, written the usual way, overlapping no other."""
    if change.operation in ("delete", "restore"):
        return
    given = change.values.get("cidr")
    if given is None:
        return
    try:
        net = ipaddress.ip_network(str(given).strip(), strict=False)
    except ValueError as error:
        raise _invalid("cidr", "A network like 192.168.1.0/24.") from error
    change.values["cidr"] = str(net)
    for other in await _every(Client.of(change), "ipam.subnet"):
        if other["sys_id"] == change.sys_id:
            continue
        theirs = ipaddress.ip_network(str(other["cidr"]))
        if theirs.version == net.version and theirs.overlaps(net):
            raise _invalid("cidr", f"It overlaps {theirs}.")
    for name in ("gateway", "dhcp_from", "dhcp_to"):
        value = change.values.get(name)
        if value and ipaddress.ip_address(str(value)) not in net:
            raise _invalid(name, f"Inside {net}.")


async def _subnet_of(client: Client, address: Any) -> Any:
    for s in await _every(client, "ipam.subnet"):
        if address in ipaddress.ip_network(str(s["cidr"])):
            return s["sys_id"]
    return None


async def _check_address(change: Change) -> None:
    """An address written the usual way; its subnet worked out."""
    if change.operation in ("delete", "restore") or "address" not in change.values:
        return
    try:
        address = ipaddress.ip_address(str(change.values["address"]).strip())
    except ValueError as error:
        raise _invalid("address", "An address like 192.168.1.20.") from error
    change.values["address"] = str(address)
    change.values["subnet"] = await _subnet_of(Client.of(change), address)


async def _link_record(change: Change) -> None:
    """An A or AAAA record points to its address, when IPAM knows it."""
    if change.operation in ("delete", "restore"):
        return
    kind, value = change.values.get("type"), change.values.get("value")
    if kind in ("a", "aaaa") and value:
        found = await Client.of(change).list(
            "ipam.address", where={"field": "address", "op": "eq", "value": str(value)}, limit=1
        )
        change.values["address"] = found["records"][0]["sys_id"] if found["records"] else None


async def next_free(client: Client, subnet: str) -> str:
    """The first address of the subnet nobody uses (not the gateway, not in its DHCP range)."""
    s = await client.get("ipam.subnet", subnet)
    net = ipaddress.ip_network(str(s["cidr"]))
    used = {
        str(a["address"])
        for a in await _every(
            client, "ipam.address", {"field": "subnet", "op": "eq", "value": subnet}
        )
    }
    if s.get("gateway"):
        used.add(str(s["gateway"]))
    low = int(ipaddress.ip_address(s["dhcp_from"])) if s.get("dhcp_from") else None
    high = int(ipaddress.ip_address(s["dhcp_to"])) if s.get("dhcp_to") else None
    for n, host in enumerate(net.hosts()):
        if n >= MAX_SCAN:
            break
        if str(host) in used or (low is not None and high is not None and low <= int(host) <= high):
            continue
        return str(host)
    raise Problem(409, "full", f"No free address in {net}.")


async def _next_route(request: RouteRequest) -> dict[str, Any]:
    subnet = request.query.get("subnet")
    if not subnet:
        raise Problem(422, "validation", "Pick a subnet.")
    return {"address": await next_free(Client.of(request), subnet)}


async def _take_next(run: ActionRun) -> ActionResult:
    client = Client(run.records)
    address = await next_free(client, str(run.sys_id))
    await client.create("ipam.address", {"address": address, "kind": "reserved"})
    return ActionResult(message=f"{address} is yours: it's reserved now.")


# ---- reading zones ----


async def _replace(client: Client, zone: dict[str, Any], records: list[dict[str, Any]]) -> int:
    """The zone's read records as they are now: new ones made, changed ones updated, gone ones
    deleted; records typed by hand stay."""
    have = {
        (r["name"], r["type"], r["value"]): r
        for r in await _every(
            client, "ipam.dns_record", {"field": "zone", "op": "eq", "value": zone["sys_id"]}
        )
        if r["origin"] != "manual"
    }
    seen: set[tuple[str, str, str]] = set()
    for r in records:
        key = (r["name"], r["type"], r["value"])
        seen.add(key)
        if key not in have:
            await client.create("ipam.dns_record", r | {"zone": zone["sys_id"]})
        elif have[key].get("ttl") != r.get("ttl"):
            await client.update(
                "ipam.dns_record", have[key]["sys_id"], {"ttl": r.get("ttl")}, have[key]["_etag"]
            )
    for key, r in have.items():
        if key not in seen:
            await client.delete("ipam.dns_record", r["sys_id"], r["_etag"])
    return len(records)


def dns_server(given: Any) -> str:
    """A DNS server to transfer zones from: an address in your own network, not this machine
    itself or its network links."""
    try:
        ip = ipaddress.ip_address(str(given or "").strip())
    except ValueError:
        raise _invalid("server", "The DNS server's IP address, like 192.168.1.1.") from None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise _invalid("server", "A DNS server in your own network, like 192.168.1.1.")
    return str(ip)


async def _check_zone(change: Change) -> None:
    if change.values.get("server"):
        change.values["server"] = dns_server(change.values["server"])


def _transfer(server: str, name: str) -> list[dict[str, Any]]:
    zone = dns.zone.from_xfr(dns.query.xfr(server, name, lifetime=20))
    found: list[dict[str, Any]] = []
    for owner, ttl, rdata in zone.iterate_rdatas():
        kind = dns.rdatatype.to_text(rdata.rdtype).lower()
        if kind not in RECORD_TYPES:
            continue
        label = str(owner)
        found.append(
            {
                "name": name if label == "@" else f"{label}.{name}",
                "type": kind,
                "value": rdata.to_text(),
                "ttl": int(ttl),
                "origin": "axfr",
            }
        )
    return found


async def read_zones(run: JobRun) -> str:
    """Zones kept by zone transfer or UniFi, read again."""
    client = Client(run.records)
    read = 0
    for zone in await _every(
        client, "ipam.dns_zone", {"field": "kept_by", "op": "ne", "value": "manual"}
    ):
        records: list[dict[str, Any]] = []
        if zone["kept_by"] == "axfr":
            try:
                server = dns_server(zone.get("server"))
            except Problem as error:
                raise Problem(
                    409, "bad_server", f"{zone['name']}: a DNS server in your network."
                ) from error
            try:
                records = await asyncio.to_thread(_transfer, server, str(zone["name"]))
            except (dns.exception.DNSException, OSError) as error:
                raise Problem(502, "transfer_failed", f"{zone['name']}: {error}") from error
        elif zone["kept_by"] == "unifi":
            async with client.connection("ipam.unifi") as http:
                answer = await http.get("/proxy/network/v2/api/site/default/static-dns")
                answer.raise_for_status()
                for r in answer.json():
                    name = str(r.get("key") or "")
                    if not name.endswith(str(zone["name"])) and r.get("enabled", True):
                        continue
                    records.append(
                        {
                            "name": name,
                            "type": str(r.get("record_type") or "A").lower(),
                            "value": str(r.get("value") or ""),
                            "ttl": r.get("ttl") or None,
                            "origin": "unifi",
                        }
                    )
        await _replace(client, zone, records)
        current = await client.get("ipam.dns_zone", zone["sys_id"])
        await client.update(
            "ipam.dns_zone",
            zone["sys_id"],
            {"read": datetime.now(UTC).isoformat()},
            current["_etag"],
        )
        read += 1
    return f"{read} zone(s) read."


module = Module(
    id="ipam",
    name="IPAM",
    publisher="p42",
    version=package_version("p42cloud-ipam"),
    depends_on={"sys": ">=0.52.1,<1", "places": ">=0.1,<1", "cmdb": ">=0.1,<1"},
    tables=TABLES,
    roles=(
        Role(
            "ipam.reader",
            "IPAM reader",
            grants=tuple(Grant(f"ipam.{t.name}", ("read",)) for t in TABLES),
            includes=("places.reader",),
            description="Sees VLANs, subnets, addresses and DNS records.",
        ),
        Role(
            "ipam.manager",
            "IPAM manager",
            grants=tuple(Grant(f"ipam.{t.name}", ALL) for t in TABLES),
            includes=("ipam.reader",),
            description="Keeps VLANs, subnets, addresses and DNS records.",
        ),
    ),
    hooks=(
        TableHook("ipam.subnet", before=_check_subnet),
        TableHook("ipam.address", before=_check_address),
        TableHook("ipam.dns_record", before=_link_record),
        TableHook("ipam.dns_zone", before=_check_zone),
    ),
    connection_types=(
        ConnectionType(
            "unifi",
            "UniFi console: DNS records for IPAM",
            (),
            auth="api_key",
            secrets=("key",),
            api_key_header="X-API-KEY",
            shared=True,
            local=True,
        ),
    ),
    jobs=(Job("zones", "Read DNS zones", read_zones, timeout=300),),
    schedules=(Schedule("zones", "DNS zones, daily", "zones", "40 5 * * *"),),
    routes=(Route("GET", "/next_free", _next_route, "The next free address of a subnet."),),
    actions=(
        UIAction(
            "take_next",
            "ipam.subnet",
            "Reserve the next free address",
            places=("form_button",),
            icon="add_location",
            handler=_take_next,
        ),
    ),
    widgets=(
        Widget(
            "by_kind",
            "Addresses by kind",
            "chart",
            dashboard="ipam",
            list="ipam.address",
            group="kind",
            chart="donut",
            icon="lan",
        ),
    ),
    views=(
        ListView("ipam.vlan", ("number", "name", "site"), sort=("number",)),
        ListView(
            "ipam.subnet",
            ("cidr", "name", "vlan", "place", "gateway"),
            sort=("cidr",),
            search=("cidr", "name"),
        ),
        ListView(
            "ipam.address",
            ("address", "subnet", "kind", "used_by", "dns_name", "last_seen"),
            sort=("address",),
            search=("address", "dns_name", "used_by"),
        ),
        ListView("ipam.dns_zone", ("name", "kept_by", "serial", "read"), sort=("name",)),
        ListView(
            "ipam.dns_record",
            ("name", "type", "value", "ttl", "zone", "origin"),
            sort=("name",),
            search=("name", "value"),
        ),
    ),
    forms=(
        FormView(
            "ipam.subnet",
            (
                FormSection("Subnet", ("cidr", "name", "vlan", "place")),
                FormSection("Addresses", ("gateway", "dhcp_from", "dhcp_to")),
                FormSection("Notes", ("notes",), columns=1),
            ),
            related=(RelatedList("ipam.address", "subnet"),),
        ),
        FormView(
            "ipam.dns_zone",
            (
                FormSection("Zone", ("name", "kept_by", "serial", "read")),
                FormSection("Zone transfer", ("server",)),
                FormSection("Notes", ("notes",), columns=1),
            ),
            related=(RelatedList("ipam.dns_record", "zone"),),
        ),
    ),
    navigation=(
        Section(
            "IPAM",
            (
                Item(
                    "dashboard", "Dashboard", kind="page", path="/dashboard/ipam", icon="dashboard"
                ),
                Item("subnets", "Subnets", "ipam.subnet", icon="lan"),
                Item("addresses", "IP addresses", "ipam.address", icon="pin"),
                Item("vlans", "VLANs", "ipam.vlan", icon="segment"),
                Item("zones", "DNS zones", "ipam.dns_zone", icon="dns"),
                Item("records", "DNS records", "ipam.dns_record", icon="list_alt"),
            ),
        ),
    ),
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="VLANs, subnets, IP addresses and DNS zones and records.",
    category="IT",
    icon="lan",
)
