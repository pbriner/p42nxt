"""Ports and cables: a port takes one cable (a patch panel's port two: front and back), and the
path from a device's port through patch panels to the switch port is followed and shown."""

from typing import Any

from p42cloud.sdk import Change, Client, Problem, RouteRequest

from p42cloud_cmdb.index import every, invalid

MAX_HOPS = 12


async def _is_panel(client: Client, port: dict[str, Any]) -> bool:
    ci = await client.get("cmdb.ci", str(port["ci"]))
    return ci["ci_class"] == "patch_panel"


async def _cables_at(client: Client, port: str) -> list[dict[str, Any]]:
    return [
        *await every(client, "cmdb.cable", {"field": "from_port", "op": "eq", "value": port}),
        *await every(client, "cmdb.cable", {"field": "to_port", "op": "eq", "value": port}),
    ]


async def check_cable(change: Change) -> None:
    """Two different ports, each with room for one more cable."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    current = await client.get("cmdb.cable", change.sys_id) if change.sys_id else {}
    merged = current | change.values
    a, b = str(merged["from_port"]), str(merged["to_port"])
    if a == b:
        raise invalid("to_port", "A cable links two different ports.")
    for port_id, field in ((a, "from_port"), (b, "to_port")):
        port = await client.get("cmdb.port", port_id)
        taken = [c for c in await _cables_at(client, port_id) if c["sys_id"] != change.sys_id]
        room = 2 if await _is_panel(client, port) else 1
        if len(taken) >= room:
            raise invalid(field, f"Port {port['name']} has its cable already.")


async def path(client: Client, start: str) -> list[dict[str, Any]]:
    """From a port along its cables, through patch panels, to the far end."""
    hops: list[dict[str, Any]] = []
    port_id = start
    came: str | None = None
    for _ in range(MAX_HOPS):
        port = await client.get("cmdb.port", port_id)
        ci = await client.get("cmdb.ci", str(port["ci"]))
        hops.append({"port": port["name"], "item": ci["name"], "ci": ci["sys_id"]})
        cables = [c for c in await _cables_at(client, port_id) if c["sys_id"] != came]
        if not cables or (came is not None and ci["ci_class"] != "patch_panel"):
            break
        cable = cables[0]
        came = cable["sys_id"]
        ends = (str(cable["from_port"]), str(cable["to_port"]))
        port_id = ends[1] if ends[0] == port_id else ends[0]
    return hops


async def path_route(request: RouteRequest) -> dict[str, Any]:
    start = request.query.get("port")
    if not start:
        raise Problem(422, "validation", "Pick a port.")
    hops = await path(Client.of(request), start)
    return {"hops": hops, "text": " \u203a ".join(f"{h['item']} {h['port']}" for h in hops)}
