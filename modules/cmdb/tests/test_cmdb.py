"""The CMDB on its own installation: every class indexed, relationships checked, maps both ways,
the review list, cables and their path, certificates read and warned of, the installation
registering itself, and agents as items."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from p42cloud.sdk import Client, Problem

from p42cloud_cmdb.discovery import _shelly_state, observe  # pyright: ignore[reportPrivateUsage]
from p42cloud_cmdb.index import graph
from p42cloud_cmdb.network import path

Acting = Callable[..., AbstractAsyncContextManager[Client]]
RunJob = Callable[[str], Awaitable[tuple[str, str | None]]]


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def _ci(client: Client, key: str, values: dict[str, Any]) -> tuple[dict[str, Any], str]:
    made = await client.create(f"cmdb.{key}", values)
    found = await client.list(
        "cmdb.ci",
        where={
            "and": [
                {"field": "table", "op": "eq", "value": f"cmdb.{key}"},
                {"field": "record", "op": "eq", "value": str(made["sys_id"])},
            ]
        },
    )
    return made, str(found["records"][0]["sys_id"])


async def _type(client: Client, name: str) -> str:
    found = await client.list(
        "cmdb.relation_type", where={"field": "name", "op": "eq", "value": name}
    )
    return str(found["records"][0]["sys_id"])


async def _link(client: Client, a: str, kind: str, b: str) -> dict[str, Any]:
    return await client.create(
        "cmdb.relationship", {"from_ci": a, "type": await _type(client, kind), "to_ci": b}
    )


async def test_every_class_is_indexed_and_follows_its_item(as_system: Acting) -> None:
    async with as_system() as client:
        server, server_ci = await _ci(client, "server", {"name": f"srv {_tag()}"})
        index = await client.get("cmdb.ci", server_ci)
        assert (index["ci_class"], index["family"], index["number"][:3]) == (
            "server",
            "hardware",
            "CI-",
        )
        current = await client.get("cmdb.server", server["sys_id"])
        await client.update(
            "cmdb.server", server["sys_id"], {"status": "in_repair"}, current["_etag"]
        )
        assert (await client.get("cmdb.ci", server_ci))["status"] == "in_repair"
        current = await client.get("cmdb.server", server["sys_id"])
        await client.delete("cmdb.server", server["sys_id"], current["_etag"])
        gone = await client.list(
            "cmdb.ci", where={"field": "sys_id", "op": "eq", "value": server_ci}, deleted=True
        )
        assert gone["records"]


async def test_relationships_are_checked(as_system: Acting) -> None:
    async with as_system() as client:
        _, vm = await _ci(client, "vm", {"name": f"vm {_tag()}"})
        _, host = await _ci(client, "server", {"name": f"host {_tag()}"})
        _, cert = await _ci(client, "certificate", {"name": f"cert {_tag()}"})
        _, switch = await _ci(client, "network_device", {"name": f"sw {_tag()}", "kind": "switch"})
        _, router = await _ci(client, "network_device", {"name": f"rt {_tag()}", "kind": "router"})
        await _link(client, vm, "runs_on", host)
        for a, kind, b in (
            (vm, "runs_on", vm),  # not to itself
            (cert, "runs_on", host),  # a certificate doesn't run on anything
            (host, "secured_by", vm),  # secured by a certificate only
            (host, "depends_on", vm),  # a loop: the VM runs on the host
        ):
            with pytest.raises(Problem):
                await _link(client, a, kind, b)
        await _link(client, switch, "connected_to", router)
        with pytest.raises(Problem):  # reads both ways: once is enough
            await _link(client, router, "connected_to", switch)


async def test_maps_show_what_needs_what(as_system: Acting) -> None:
    async with as_system() as client:
        _, container = await _ci(client, "container", {"name": f"web {_tag()}"})
        _, engine = await _ci(client, "container_engine", {"name": f"docker {_tag()}"})
        _, vm = await _ci(client, "vm", {"name": f"vm {_tag()}"})
        _, host = await _ci(client, "server", {"name": f"pve {_tag()}"})
        _, db = await _ci(client, "database", {"name": f"shop {_tag()}"})
        _, cert = await _ci(client, "certificate", {"name": f"shop.example {_tag()}"})
        _, app = await _ci(client, "business_application", {"name": f"Shop {_tag()}"})
        await _link(client, container, "runs_on", engine)
        await _link(client, engine, "runs_on", vm)
        await _link(client, vm, "runs_on", host)
        await _link(client, container, "uses", db)
        await _link(client, container, "secured_by", cert)
        await _link(client, app, "depends_on", container)
        down = await graph(client, container, "down", 6)
        up = await graph(client, host, "up", 6, needs_only=True)
    levels = {n["id"]: n["level"] for n in down["nodes"]}
    assert (levels[engine], levels[vm], levels[host], levels[db], levels[cert]) == (1, 2, 3, 1, 1)
    assert {n["id"] for n in up["nodes"]} >= {vm, engine, container, app}
    assert {n["id"]: n["level"] for n in up["nodes"]}[app] == -4  # impact reaches the business


async def test_what_scans_find_goes_to_the_review_list(as_system: Acting) -> None:
    tag = _tag()
    async with as_system() as client:
        known, _ = await _ci(client, "server", {"name": "known", "serial": f"S{tag}"})
        assert await observe(client, "UniFi", "server", {"serial": f"S{tag}", "os": "Debian"}) == (
            "matched"
        )
        updated = await client.get("cmdb.server", known["sys_id"])
        assert (updated["os"], updated["scanned"], updated["last_seen"] is not None) == (
            "Debian",
            ["os"],
            True,
        )
        facts = {
            "name": f"ap {tag}",
            "mac": f"aa:bb:cc:{tag[:2]}:{tag[2:4]}:{tag[4:]}",
            "discovery_key": f"unifi:{tag}",
        }
        assert await observe(client, "UniFi", "network_device", facts) == "waiting"
        assert await observe(client, "UniFi", "network_device", facts) == "waiting"  # counted
        row = (
            await client.list(
                "cmdb.review", where={"field": "match_key", "op": "eq", "value": facts["mac"]}
            )
        )["records"][0]
        assert row["times"] == 2
        done = await client.action(
            "cmdb.review", row["sys_id"], "cmdb.review_create", {"ci_class": "network_device"}
        )
        assert "Made" in str(done["message"])
        assert await observe(client, "UniFi", "network_device", facts) == "matched"  # by its MAC


async def test_a_cable_path_runs_through_patch_panels(as_system: Acting) -> None:
    async with as_system() as client:
        _, pc = await _ci(client, "computer", {"name": f"PC {_tag()}"})
        _, panel = await _ci(client, "patch_panel", {"name": f"Panel {_tag()}"})
        _, switch = await _ci(client, "network_device", {"name": f"SW {_tag()}", "kind": "switch"})
        nic = await client.create("cmdb.port", {"ci": pc, "name": "eth0"})
        p12 = await client.create("cmdb.port", {"ci": panel, "name": "12"})
        gi = await client.create("cmdb.port", {"ci": switch, "name": "Gi0/12"})
        await client.create("cmdb.cable", {"from_port": nic["sys_id"], "to_port": p12["sys_id"]})
        await client.create("cmdb.cable", {"from_port": p12["sys_id"], "to_port": gi["sys_id"]})
        with pytest.raises(Problem):  # the PC's port has its cable
            await client.create("cmdb.cable", {"from_port": nic["sys_id"], "to_port": gi["sys_id"]})
        hops = await path(client, str(nic["sys_id"]))
    assert [h["port"] for h in hops] == ["eth0", "12", "Gi0/12"]


def _pem(days: int) -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "shop.example.com")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("shop.example.com")]), False)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


async def test_a_certificate_is_read_and_warned_of(as_system: Acting, run_job: RunJob) -> None:
    async with as_system() as client:
        made = await client.create("cmdb.certificate", {"name": f"shop {_tag()}", "pem": _pem(10)})
        assert (made["common_name"], made["alt_names"]) == ("shop.example.com", "shop.example.com")
        with pytest.raises(Problem):
            await client.create(
                "cmdb.certificate", {"name": "key", "pem": "-----BEGIN PRIVATE KEY-----"}
            )
    state, said = await run_job("cmdb.certificates")
    assert state == "succeeded" and said is not None
    async with as_system() as client:
        assert (await client.get("cmdb.certificate", made["sys_id"]))["warned"] == 14
    await run_job("cmdb.certificates")  # not told twice for the same mark
    async with as_system() as client:
        assert (await client.get("cmdb.certificate", made["sys_id"]))["warned"] == 14


async def test_the_installation_registers_itself(as_system: Acting, run_job: RunJob) -> None:
    state, said = await run_job("cmdb.register")
    assert state == "succeeded" and str(said).startswith("Registered P42cloud")
    await run_job("cmdb.register")  # again: nothing made twice
    async with as_system() as client:
        apps = await client.list(
            "cmdb.application", where={"field": "discovery_key", "op": "eq", "value": "p42:app"}
        )
        assert len(apps["records"]) == 1
        modules = await client.list(
            "cmdb.application",
            where={"field": "discovery_key", "op": "starts_with", "value": "p42:module:"},
        )
        assert {m["discovery_key"] for m in modules["records"]} >= {"p42:module:cmdb"}


def test_a_shellys_state_is_read_from_both_generations() -> None:
    gen2 = {
        "switch:0": {
            "output": True,
            "apower": 12.5,
            "aenergy": {"total": 1500},
            "temperature": {"tC": 40},
        }
    }
    gen1 = {"relays": [{"ison": False}], "meters": [{"power": 0}], "temperature": 30}
    assert _shelly_state(gen2) == {
        "switched_on": True,
        "power_w": 12.5,
        "energy_kwh": 1.5,
        "temperature": 40,
    }
    assert _shelly_state(gen1)["switched_on"] is False


async def _person(client: Client, role: str) -> str:
    found = await client.list("sys.role", where={"field": "name", "op": "eq", "value": role})
    group = await client.create("sys.group", {"name": f"{role} {_tag()}"})
    await client.create(
        "sys.group_role", {"group": group["sys_id"], "role": found["records"][0]["sys_id"]}
    )
    person = await client.create("sys.person", {"name": f"{role} {_tag()}"})
    await client.create("sys.membership", {"group": group["sys_id"], "person": person["sys_id"]})
    return str(person["sys_id"])


async def test_a_shelly_is_reached_by_its_address_only(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        for bad in ("10.0.0.5:8080/secret?x=", "169.254.169.254", "127.0.0.1", "8.8.8.8"):
            with pytest.raises(Problem):
                await client.create("cmdb.shelly_device", {"name": "plug", "address": bad})
        plug = await client.create(
            "cmdb.shelly_device", {"name": f"plug {_tag()}", "address": " 192.168.1.40 "}
        )
        assert plug["address"] == "192.168.1.40"
        operator = await _person(client, "cmdb.operator")
    async with as_person(operator) as client:
        current = await client.get("cmdb.shelly_device", plug["sys_id"])
        with pytest.raises(Problem):  # switching, not moving it elsewhere
            await client.update(
                "cmdb.shelly_device", plug["sys_id"], {"address": "192.168.1.41"}, current["_etag"]
            )
        with pytest.raises(Problem):  # agents' answers are for managers
            await client.list("sys.agent_task")
