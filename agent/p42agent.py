"""The p42 agent: works for a P42cloud installation from another network, through its public API
and only outbound. Enrol once with a one-time code from the installation (Platform → Agents),
then run it as a service; it fetches its tasks every minute, does them and answers.

    python p42agent.py enrol https://p42.example.com ABCD-1234-EF56-7890
    python p42agent.py run

What it does is only what the tasks it knows ask: today a Shelly device's state, and switching
one. It keeps its token in ~/.p42agent.json (readable by its user only), talks to the
installation only over https, and reaches devices only by an address in its own network (or the
ranges in P42AGENT_RANGES, like "192.168.1.0/24,10.0.5.0/24").
"""

import ipaddress
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx

VERSION = "0.1.0"
STATE = Path(os.environ.get("P42AGENT_STATE", Path.home() / ".p42agent.json"))
EVERY = int(os.environ.get("P42AGENT_EVERY", "60"))


RANGES = [
    ipaddress.ip_network(r.strip(), strict=False)
    for r in os.environ.get("P42AGENT_RANGES", "").split(",")
    if r.strip()
]
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_NAME = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})*$")


def _save(data: dict[str, Any]) -> None:
    # Made readable by its user only from the start, never for a moment by others.
    handle = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(handle, 0o600)
    with os.fdopen(handle, "w") as file:
        json.dump(data, file)


def _https(url: str) -> str:
    url = url.rstrip("/")
    if httpx.URL(url).scheme != "https":
        raise SystemExit("The installation's address must start with https://.")
    return url


def enrol(url: str, code: str) -> None:
    url = _https(url)
    answer = httpx.post(
        f"{url}/api/v1/agents/enrol",
        json={"code": code, "version": VERSION, "host": socket.gethostname()},
        timeout=20,
    )
    answer.raise_for_status()
    found = answer.json()
    _save({"url": url, "token": found["token"], "name": found["name"]})
    print(f"Enrolled as {found['name']}.")


def _device(given: Any) -> str:
    """A device's address as given by the installation: an address in this network or a host
    name, nothing more (no port, path or user)."""
    text = str(given or "").strip().strip("[]")
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        if _NAME.match(text):
            return text
        raise ValueError("Not a device address.") from None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    allowed = any(ip in r for r in RANGES) if RANGES else ip.is_private
    if not allowed or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise ValueError("Not an address this agent reaches.")
    return str(ip)


def _shelly(payload: dict[str, Any]) -> dict[str, Any]:
    host, gen2 = (
        _device(payload.get("address")),
        int(payload.get("generation") or 2) >= 2,
    )
    query: dict[str, str] = {}
    if "on" in payload:
        on = bool(payload["on"])
        path = "/rpc/Switch.Set" if gen2 else "/relay/0"
        query = (
            {"id": "0", "on": str(on).lower()}
            if gen2
            else {"turn": "on" if on else "off"}
        )
    else:
        path = "/rpc/Shelly.GetStatus" if gen2 else "/status"
    answer = httpx.get(
        httpx.URL(scheme="http", host=host, path=path, params=query), timeout=5
    )
    answer.raise_for_status()
    found = answer.json()
    # Only what the installation reads from a Shelly, not whatever else the device says.
    keep = ("switch:0",) if gen2 else ("relays", "meters", "temperature")
    return {k: found[k] for k in keep if isinstance(found, dict) and k in found}


KNOWN = {"shelly_status": _shelly, "shelly_switch": _shelly}


def run() -> None:
    state = json.loads(STATE.read_text())
    state["url"] = _https(state["url"])
    headers = {"Authorization": f"Bearer {state['token']}"}
    with httpx.Client(base_url=state["url"], headers=headers, timeout=30) as api:
        while True:
            try:
                tasks = api.get(
                    "/api/v1/agents/tasks", params={"version": VERSION}
                ).json()["tasks"]
                for task in tasks:
                    work = KNOWN.get(task["kind"])
                    try:
                        if work is None:
                            raise ValueError(f"I don't know {task['kind']}.")
                        body = {"ok": True, "result": work(task["payload"] or {})}
                    except (httpx.HTTPError, ValueError, KeyError) as error:
                        body = {"ok": False, "result": {"error": str(error)[:200]}}
                    api.post(f"/api/v1/agents/tasks/{task['task']}", json=body)
            except httpx.HTTPError as error:
                print(f"Not reached: {error}", file=sys.stderr)
            time.sleep(EVERY)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "enrol":
        enrol(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2 and sys.argv[1] == "run":
        run()
    else:
        print(__doc__)
        sys.exit(2)
