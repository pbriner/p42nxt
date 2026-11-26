"""What the CMDB finds by itself: devices from UniFi and Proxmox, Shelly devices' state, the
installation registering itself, and certificates read from their PEM and warned before they
expire. New things always go to the review list; scans never overwrite what was typed.
"""

import hashlib
import ipaddress
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import NameOID
from p42cloud.sdk import (
    ActionResult,
    ActionRun,
    Change,
    Client,
    ConnectionType,
    EventRun,
    JobRun,
    Problem,
)

from p42cloud_cmdb.classes import BY_KEY
from p42cloud_cmdb.index import every, invalid

DOWN_AFTER = timedelta(minutes=15)
# "Last seen" is written at most this often unless something else changes with it: each write is
# an audit entry, and a scan every few minutes would fill the trail with nothing but that.
SEEN_EVERY = timedelta(minutes=10)
GEN2 = 2  # Shelly's second generation and later answer RPC
WARN_DAYS = (30, 14, 3)

# ---- connections to the own network ----


def _proxmox_sign(request: httpx.Request, secrets: dict[str, str]) -> None:
    request.headers["Authorization"] = (
        f"PVEAPIToken={secrets.get('token_id', '')}={secrets.get('secret', '')}"
    )


CONNECTION_TYPES = (
    ConnectionType(
        "unifi",
        "UniFi console: devices for the CMDB",
        (),
        auth="api_key",
        secrets=("key",),
        api_key_header="X-API-KEY",
        shared=True,
        local=True,
    ),
    ConnectionType(
        "proxmox",
        "Proxmox",
        (),
        auth="signed",
        secrets=("token_id", "secret"),
        sign=_proxmox_sign,
        shared=True,
        local=True,
    ),
    ConnectionType("shelly", "Shelly devices", (), shared=True, local=True),
)


# ---- the review list ----


async def _matching(client: Client, facts: dict[str, Any]) -> Any:
    """The CI a scan's facts belong to: by serial, else MAC, else host name, else its key."""
    for field in ("serial", "host_name", "discovery_key"):
        value = facts.get(field)
        if not value:
            continue
        for key, c in BY_KEY.items():
            if field != "discovery_key" and field not in c.own and field != "serial":
                continue
            found = await client.list(
                f"cmdb.{key}", where={"field": field, "op": "eq", "value": str(value)}, limit=1
            )
            if found["records"]:
                return key, found["records"][0]
    mac = facts.get("mac")
    if mac:
        found = await client.list(
            "cmdb.interface", where={"field": "mac", "op": "eq", "value": str(mac).lower()}, limit=1
        )
        if found["records"]:
            ci = await client.get("cmdb.ci", str(found["records"][0]["ci"]))
            return ci["ci_class"], await client.get(ci["table"], ci["record"])
    return None


def _stale(seen: Any, now: datetime) -> bool:
    """Whether a stored "last seen" is old enough to be written again."""
    if not seen:
        return True
    try:
        was = datetime.fromisoformat(str(seen))
    except ValueError:
        return True
    return now - was >= SEEN_EVERY


def _searched(facts: dict[str, Any]) -> dict[str, Any]:
    """A review row's address, MAC and host name from its facts, for its list and search."""
    address = facts.get("address") or facts.get("management_address")
    mac = facts.get("mac")
    host = facts.get("host_name")
    return {
        "address": str(address)[:64] if address else None,
        "mac": str(mac).lower()[:32] if mac else None,
        "host_name": str(host)[:253] if host else None,
    }


async def observe(client: Client, source: str, ci_class: str, facts: dict[str, Any]) -> str:
    """One thing a scan saw: its CI updated (only the scanned fields), else on the review list.
    Returns matched, waiting or ignored."""
    moment = datetime.now(UTC)
    now = moment.isoformat()
    found = await _matching(client, facts)
    if found is not None:
        key, record = found
        own: set[str] = (
            set(BY_KEY[key].own) | {"serial", "model", "last_seen"} if key in BY_KEY else set()
        )
        scanned = {k: v for k, v in facts.items() if k in own and v not in (None, "")}
        changed = {k: v for k, v in scanned.items() if str(record.get(k)) != str(v)}
        if "last_seen" in own and (changed or _stale(record.get("last_seen"), moment)):
            changed["last_seen"] = now
        if changed:
            names = sorted(set(record.get("scanned") or []) | (set(changed) - {"last_seen"}))
            await client.update(
                f"cmdb.{key}", record["sys_id"], changed | {"scanned": names}, record["_etag"]
            )
        return "matched"
    match_key = str(
        facts.get("serial") or facts.get("mac") or facts.get("host_name") or facts["discovery_key"]
    )
    waiting = await client.list(
        "cmdb.review", where={"field": "match_key", "op": "eq", "value": match_key}, limit=1
    )
    if waiting["records"]:
        row = waiting["records"][0]
        await client.update(
            "cmdb.review",
            row["sys_id"],
            {"seen": now, "times": int(row["times"]) + 1, "facts": facts} | _searched(facts),
            row["_etag"],
        )
        return str(row["state"])
    await client.create(
        "cmdb.review",
        {
            "found": str(facts.get("name") or match_key)[:255],
            "found_by": source,
            "ci_class": ci_class,
            "facts": facts,
            "match_key": match_key,
            "seen": now,
        }
        | _searched(facts),
    )
    await client.publish("cmdb.review_waiting", data={"found": str(facts.get("name") or "")})
    return "waiting"


async def create_from_review(run: ActionRun) -> ActionResult:
    """Makes the CI the review row describes, of the class chosen (or proposed)."""
    client = Client(run.records)
    row = run.record
    key = str(run.inputs.get("ci_class") or row.get("ci_class") or "")
    if key not in BY_KEY:
        raise invalid("ci_class", "Choose one of the classes.")
    facts: dict[str, Any] = dict(row.get("facts") or {})
    fields = set(BY_KEY[key].own) | {"name", "serial", "model", "discovery_key", "place"}
    values = {k: v for k, v in facts.items() if k in fields and v not in (None, "")}
    values.setdefault("name", row["found"])
    values["discovery_key"] = row["match_key"]
    if run.inputs.get("place"):
        values["place"] = run.inputs["place"]
    made = await client.create(f"cmdb.{key}", values)
    index = await client.list(
        "cmdb.ci",
        where={
            "and": [
                {"field": "table", "op": "eq", "value": f"cmdb.{key}"},
                {"field": "record", "op": "eq", "value": str(made["sys_id"])},
            ]
        },
        limit=1,
    )
    ci = index["records"][0]["sys_id"] if index["records"] else None
    if ci and facts.get("mac"):
        await client.create(
            "cmdb.interface", {"ci": ci, "name": "eth0", "mac": str(facts["mac"]).lower()}
        )
    current = await client.get("cmdb.review", row["sys_id"])
    # Linked, so the review row leads to what it became.
    await client.update(
        "cmdb.review", row["sys_id"], {"state": "created", "ci": ci}, current["_etag"]
    )
    return ActionResult(message=f"Made {values['name']}.")


async def attach_review(run: ActionRun) -> ActionResult:
    """It's one we know: the CI chosen finds it again from now on."""
    client = Client(run.records)
    ci = await client.get("cmdb.ci", str(run.inputs["ci"]))
    record = await client.get(ci["table"], ci["record"])
    await client.update(
        ci["table"], ci["record"], {"discovery_key": run.record["match_key"]}, record["_etag"]
    )
    current = await client.get("cmdb.review", run.record["sys_id"])
    await client.update(
        "cmdb.review",
        run.record["sys_id"],
        {"state": "attached", "ci": ci["sys_id"]},
        current["_etag"],
    )
    return ActionResult(message=f"Attached to {ci['name']}.")


# ---- UniFi and Proxmox ----


async def _get(http: httpx.AsyncClient, path: str) -> Any:
    answer = await http.get(path)
    answer.raise_for_status()
    return answer.json()


async def unifi_scan(run: JobRun) -> str:
    """The UniFi console's devices (switches, access points, gateways) and its clients."""
    client = Client(run.records)
    counts = {"matched": 0, "waiting": 0, "ignored": 0, "created": 0, "attached": 0}
    try:
        async with client.connection("cmdb.unifi") as http:
            sites = (await _get(http, "/proxy/network/integration/v1/sites")).get("data", [])
            for site in sites:
                base = f"/proxy/network/integration/v1/sites/{site['id']}"
                for d in (await _get(http, f"{base}/devices?limit=200")).get("data", []):
                    facts = {
                        "name": d.get("name"),
                        "model": d.get("model"),
                        "mac": str(d.get("macAddress") or "").lower() or None,
                        "management_address": d.get("ipAddress"),
                        "firmware": d.get("firmwareVersion"),
                        "discovery_key": f"unifi:{d.get('id')}",
                    }
                    counts[await observe(client, "UniFi", "network_device", facts)] += 1
                for c in (await _get(http, f"{base}/clients?limit=500")).get("data", []):
                    facts = {
                        "name": c.get("name") or c.get("hostname"),
                        "host_name": c.get("hostname"),
                        "mac": str(c.get("macAddress") or "").lower() or None,
                        "address": c.get("ipAddress"),
                        "discovery_key": f"unifi-client:{c.get('id')}",
                    }
                    counts[await observe(client, "UniFi", "computer", facts)] += 1
    except Problem as problem:
        if problem.code == "no_connection":
            return "No UniFi connection is set up: nothing to read."
        raise
    return f"UniFi: {counts['matched']} known, {counts['waiting']} to review."


async def proxmox_scan(run: JobRun) -> str:
    """Proxmox nodes, their VMs and LXC containers."""
    client = Client(run.records)
    counts = {"matched": 0, "waiting": 0, "ignored": 0, "created": 0, "attached": 0}
    try:
        async with client.connection("cmdb.proxmox") as http:
            for node in (await _get(http, "/api2/json/nodes")).get("data", []):
                name = node.get("node")
                facts = {
                    "name": name,
                    "host_name": name,
                    "hypervisor": "Proxmox",
                    "discovery_key": f"proxmox:node:{name}",
                }
                counts[await observe(client, "Proxmox", "server", facts)] += 1
                for kind, path in (("vm", "qemu"), ("lxc", "lxc")):
                    for vm in (await _get(http, f"/api2/json/nodes/{name}/{path}")).get("data", []):
                        facts = {
                            "name": vm.get("name"),
                            "kind": kind,
                            "vm_id": str(vm.get("vmid")),
                            "vcpu": vm.get("cpus"),
                            "ram_gb": round((vm.get("maxmem") or 0) / 1024**3) or None,
                            "disk_gb": round((vm.get("maxdisk") or 0) / 1024**3) or None,
                            "discovery_key": f"proxmox:{name}:{vm.get('vmid')}",
                        }
                        counts[await observe(client, "Proxmox", "vm", facts)] += 1
    except Problem as problem:
        if problem.code == "no_connection":
            return "No Proxmox connection is set up: nothing to read."
        raise
    return f"Proxmox: {counts['matched']} known, {counts['waiting']} to review."


# ---- Shelly ----


def _shelly_state(answer: dict[str, Any]) -> dict[str, Any]:
    """Gen2+ (Shelly.GetStatus) or Gen1 (/status) to the device's fields."""
    switch: dict[str, Any] = answer.get("switch:0") or {}
    energy: dict[str, Any] = switch.get("aenergy") or {}
    heat: dict[str, Any] = switch.get("temperature") or {}
    if switch:
        return {
            "switched_on": switch.get("output"),
            "power_w": switch.get("apower"),
            "energy_kwh": round(float(energy.get("total", 0)) / 1000, 3),
            "temperature": heat.get("tC"),
        }
    relays: list[dict[str, Any]] = answer.get("relays") or [{}]
    meters: list[dict[str, Any]] = answer.get("meters") or [{}]
    return {
        "switched_on": relays[0].get("ison"),
        "power_w": meters[0].get("power"),
        "temperature": answer.get("temperature"),
    }


def _changed_state(current: dict[str, Any], state: dict[str, Any], now: datetime) -> dict[str, Any]:
    """What a reading changes on a Shelly device: the values that differ, and when it was read
    and seen only with them or when that's gone stale."""
    values = {k: v for k, v in state.items() if v is not None and str(current.get(k)) != str(v)}
    if not current.get("online"):
        values["online"] = True
    if values or _stale(current.get("last_seen"), now):
        values |= {"state_read": now.isoformat(), "last_seen": now.isoformat()}
    return values


def _gone_quiet(current: dict[str, Any], now: datetime) -> bool:
    """Whether an online device that didn't answer is down. "Last seen" may lag its last answer
    by SEEN_EVERY, so only once it surely hasn't answered for DOWN_AFTER."""
    if not current.get("online"):
        return False
    seen = current.get("last_seen")
    return not seen or now - datetime.fromisoformat(str(seen)) >= DOWN_AFTER + SEEN_EVERY


async def _read_shelly(
    http: httpx.AsyncClient, address: str, generation: Any
) -> dict[str, Any] | None:
    """A Shelly device's state read here, or None if it didn't answer."""
    path = "/rpc/Shelly.GetStatus" if (generation or GEN2) >= GEN2 else "/status"
    try:
        answer = await http.get(f"http://{address}{path}", timeout=5)
        answer.raise_for_status()
        return _shelly_state(answer.json())
    except httpx.HTTPError, ValueError:
        return None


async def shelly_read(run: JobRun) -> str:
    """Every Shelly device's state; one not answering for 15 minutes is down (an event)."""
    client = Client(run.records)
    devices = await every(client, "cmdb.shelly_device")
    now = datetime.now(UTC)
    read = down = 0
    try:
        async with client.connection("cmdb.shelly") as http:
            for d in devices:
                try:
                    address = device_address(d.get("address"))
                except Problem:
                    continue  # none, or one from before addresses were checked: not reached
                if d.get("agent"):
                    # In another network: its agent reads it and answers (on_agent_task).
                    await client.agent_task(
                        str(d["agent"]),
                        "cmdb",
                        "shelly_status",
                        {
                            "device": d["sys_id"],
                            "address": address,
                            "generation": d.get("generation"),
                        },
                    )
                    continue
                state = await _read_shelly(http, address, d.get("generation"))
                current = await client.get("cmdb.shelly_device", d["sys_id"])
                if state is not None:
                    read += 1
                    values = _changed_state(current, state, now)
                    if not current.get("online"):
                        await client.publish(
                            "cmdb.device_recovered", about=("cmdb.shelly_device", d["sys_id"])
                        )
                    if values:
                        await client.update(
                            "cmdb.shelly_device", d["sys_id"], values, current["_etag"]
                        )
                elif _gone_quiet(current, now):
                    down += 1
                    await client.update(
                        "cmdb.shelly_device", d["sys_id"], {"online": False}, current["_etag"]
                    )
                    await client.publish(
                        "cmdb.device_down",
                        about=("cmdb.shelly_device", d["sys_id"]),
                        data={"name": d["name"]},
                    )
    except Problem as problem:
        if problem.code == "no_connection":
            return "No Shelly connection is set up: nothing to read."
        raise
    return f"{read} Shelly device(s) read, {down} down."


_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_NAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")


def device_address(given: Any) -> str:
    """A device's local address: an IP address or a host name, nothing more (no port, path or
    user), so what's built from it reaches the device and only the device."""
    text = str(given or "").strip()
    try:
        ip = ipaddress.ip_address(text.strip("[]"))
    except ValueError:
        if _NAME.match(text):
            return text.lower()
        raise invalid("address", "An IP address or a host name, like 192.168.1.40.") from None
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise invalid("address", "An address in your own network, like 192.168.1.40.")
    return f"[{ip}]" if ip.version == 6 else str(ip)  # noqa: PLR2004 - IPv6 in brackets


async def check_address(change: Change) -> None:
    if change.values.get("address"):
        change.values["address"] = device_address(change.values["address"])


async def _switch(run: ActionRun, on: bool | None) -> ActionResult:
    client = Client(run.records)
    d = run.record
    if not d.get("address"):
        raise invalid("address", "The device has no local address.")
    gen2 = (d.get("generation") or GEN2) >= GEN2
    if gen2:
        path = (
            "/rpc/Switch.Toggle?id=0"
            if on is None
            else f"/rpc/Switch.Set?id=0&on={str(on).lower()}"
        )
    else:
        path = "/relay/0?turn=" + ("toggle" if on is None else ("on" if on else "off"))
    async with client.connection("cmdb.shelly") as http:
        try:
            answer = await http.get(f"http://{device_address(d['address'])}{path}", timeout=5)
            answer.raise_for_status()
        except httpx.HTTPError as error:
            raise Problem(502, "unreachable", f"{d['name']} doesn't answer.") from error
    words = "toggled" if on is None else ("switched on" if on else "switched off")
    return ActionResult(message=f"{d['name']} {words}.")


async def switch_on(run: ActionRun) -> ActionResult:
    return await _switch(run, True)


async def switch_off(run: ActionRun) -> ActionResult:
    return await _switch(run, False)


async def toggle(run: ActionRun) -> ActionResult:
    return await _switch(run, None)


# ---- the installation registers itself ----


async def _keyed(client: Client, key: str, discovery_key: str, values: dict[str, Any]) -> str:
    """The CI with this key, made or brought up to date; returns its index ID."""
    found = await client.list(
        f"cmdb.{key}", where={"field": "discovery_key", "op": "eq", "value": discovery_key}, limit=1
    )
    if found["records"]:
        row = found["records"][0]
        changed = {k: v for k, v in values.items() if str(row.get(k)) != str(v)}
        if changed:
            await client.update(f"cmdb.{key}", row["sys_id"], changed, row["_etag"])
        record = row["sys_id"]
    else:
        made = await client.create(f"cmdb.{key}", values | {"discovery_key": discovery_key})
        record = made["sys_id"]
    index = await client.list(
        "cmdb.ci",
        where={
            "and": [
                {"field": "table", "op": "eq", "value": f"cmdb.{key}"},
                {"field": "record", "op": "eq", "value": str(record)},
            ]
        },
        limit=1,
    )
    return str(index["records"][0]["sys_id"])


async def _link(client: Client, a: str, type_name: str, b: str) -> None:
    kinds = await client.list(
        "cmdb.relation_type", where={"field": "name", "op": "eq", "value": type_name}, limit=1
    )
    if not kinds["records"]:
        return
    kind = kinds["records"][0]["sys_id"]
    there = await client.list(
        "cmdb.relationship",
        where={
            "and": [
                {"field": "from_ci", "op": "eq", "value": a},
                {"field": "type", "op": "eq", "value": str(kind)},
                {"field": "to_ci", "op": "eq", "value": b},
            ]
        },
        limit=1,
    )
    if not there["records"]:
        await client.create(
            "cmdb.relationship", {"from_ci": a, "type": kind, "to_ci": b, "origin": "registered"}
        )


async def register(run: JobRun) -> str:
    """The installation as CIs: the application, its modules, its database server; the setting
    "Register this installation" switches it off."""
    client = Client(run.records)
    if not await client.setting("cmdb.self_registration"):
        return "Switched off (setting)."
    facts = await client.installation()
    app = await _keyed(
        client,
        "application",
        "p42:app",
        {"name": "P42cloud", "version": facts["version"], "notes": f"Runs on {facts['host']}."},
    )
    database = await _keyed(
        client,
        "db_instance",
        "p42:database",
        {"name": "P42cloud database", "engine": "PostgreSQL", "version": facts["database"]},
    )
    await _link(client, app, "uses", database)
    for m in facts["modules"]:
        module = await _keyed(
            client,
            "application",
            f"p42:module:{m['id']}",
            {
                "name": f"P42cloud {m['name']}",
                "version": m["version"],
                "status": "in_use" if m["enabled"] else "in_stock",
            },
        )
        await _link(client, module, "part_of", app)
    return f"Registered P42cloud {facts['version']} with {len(facts['modules'])} module(s)."


# ---- certificates ----


def _name(subject: x509.Name, oid: x509.ObjectIdentifier) -> str | None:
    found = subject.get_attributes_for_oid(oid)
    return str(found[0].value) if found else None


async def read_certificate(change: Change) -> None:
    """A pasted certificate (PEM) fills its fields; the private key is never taken."""
    pem = change.values.get("pem")
    if change.operation not in ("create", "write") or not pem:
        return
    text = str(pem)
    if "PRIVATE KEY" in text:
        raise invalid("pem", "Paste the certificate only, never its private key.")
    try:
        cert = x509.load_pem_x509_certificate(text.encode())
    except ValueError as error:
        raise invalid("pem", "Not a certificate (PEM).") from error
    names: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names = [str(n) for n in ext.value.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        names = []
    common = _name(cert.subject, NameOID.COMMON_NAME)
    key = cert.public_key()
    change.values |= {
        "common_name": common,
        "alt_names": "\n".join(names) or None,
        "issuer": _name(cert.issuer, NameOID.ORGANIZATION_NAME)
        or _name(cert.issuer, NameOID.COMMON_NAME),
        "cert_serial": format(cert.serial_number, "x")[:80],
        "valid_from": cert.not_valid_before_utc.date().isoformat(),
        "valid_until": cert.not_valid_after_utc.date().isoformat(),
        "key_type": f"{type(key).__name__.removesuffix('PublicKey').removeprefix('_')}"
        f" {getattr(key, 'key_size', '')}".strip(),
        "fingerprint": cert.fingerprint(hashes.SHA256()).hex(":"),
        "warned": None,
    }
    if not change.values.get("name") and change.operation == "create":
        change.values["name"] = common or hashlib.sha256(text.encode()).hexdigest()[:12]


async def certificates_expiring(run: JobRun) -> str:
    """30, 14 and 3 days before a certificate ends, its owner and support group are told and
    an event says so; once it has ended, again."""
    client = Client(run.records)
    today = run.now.date()
    told = 0
    for c in await every(client, "cmdb.certificate", {"field": "valid_until", "op": "not_empty"}):
        until = date.fromisoformat(str(c["valid_until"]))
        left = (until - today).days
        due = next((d for d in sorted(WARN_DAYS) if left <= d), None)
        if left < 0:
            due = -1
        if due is None or (c.get("warned") is not None and int(c["warned"]) <= due):
            continue
        ended = left < 0
        title = (
            f"Certificate {c['name']} has expired"
            if ended
            else f"Certificate {c['name']} expires in {left} day(s)"
        )
        await client.notify(
            "cmdb.certificate",
            title,
            people=[c["owner"]] if c.get("owner") else [],
            groups=[c["support_group"]] if c.get("support_group") else [],
            link=f"/form/cmdb.certificate/{c['sys_id']}",
            severity="warning" if ended else "info",
        )
        await client.publish(
            "cmdb.certificate_expired" if ended else "cmdb.certificate_expiring",
            about=("cmdb.certificate", c["sys_id"]),
            data={"name": c["name"], "days": str(left)},
        )
        current = await client.get("cmdb.certificate", c["sys_id"])
        await client.update("cmdb.certificate", c["sys_id"], {"warned": due}, current["_etag"])
        told += 1
    return f"{told} certificate(s) about to expire told."


# ---- agents (the core's) as items; Shelly devices in other networks through them ----


async def on_agent(run: EventRun) -> None:
    """An agent enrolled, back or quiet: its item follows; a quiet one is a device down."""
    client = Client(run.records)
    if run.about_id is None:
        return
    agent = await client.get("sys.agent", run.about_id)
    quiet = run.type == "sys.agent_quiet"
    await _keyed(
        client,
        "agent",
        f"agent:{run.about_id}",
        {
            "name": str(agent["name"]),
            "version": agent.get("version"),
            "reported_host": agent.get("host"),
            "enrolled": agent.get("enrolled"),
            "last_seen": agent.get("last_seen"),
            "status": "in_repair" if quiet else "in_use",
        },
    )
    if quiet:
        await client.publish(
            "cmdb.device_down", about=("sys.agent", run.about_id), data={"name": agent["name"]}
        )
    elif run.type == "sys.agent_back":
        await client.publish(
            "cmdb.device_recovered", about=("sys.agent", run.about_id), data={"name": agent["name"]}
        )


async def on_agent_task(run: EventRun) -> None:
    """An agent's answer about a Shelly device: its state, as if read here."""
    if run.data.get("module") != "cmdb" or run.data.get("kind") != "shelly_status":
        return
    client = Client(run.records)
    task = await client.get("sys.agent_task", run.about_id or "")
    payload: dict[str, Any] = task.get("payload") or {}
    device_id = str(payload.get("device") or "")
    if not device_id:
        return
    current = await client.get("cmdb.shelly_device", device_id)
    if run.data.get("ok"):
        values = _changed_state(current, _shelly_state(task.get("result") or {}), datetime.now(UTC))
        if not values:
            return
    else:
        values = {"online": False}
        if current.get("online"):
            await client.publish(
                "cmdb.device_down",
                about=("cmdb.shelly_device", device_id),
                data={"name": current["name"]},
            )
    await client.update("cmdb.shelly_device", device_id, values, current["_etag"])
