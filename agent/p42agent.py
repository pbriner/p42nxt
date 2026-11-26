"""The p42 agent: works for a P42cloud installation from another network, through its public API
and only outbound. Enrol once with a one-time code from the installation (Platform → Agents),
then run it as a service; it fetches its tasks every minute, does them and answers.

    python p42agent.py enrol https://p42.example.com ABCD-1234-EF56-7890
    python p42agent.py run

What it does is only what the tasks it knows ask: today a Shelly device's state, and switching
one. It keeps its token in ~/.p42agent.json (readable by its user only).
"""

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

import httpx

VERSION = "0.1.0"
STATE = Path(os.environ.get("P42AGENT_STATE", Path.home() / ".p42agent.json"))
EVERY = int(os.environ.get("P42AGENT_EVERY", "60"))


def _save(data: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(data))
    STATE.chmod(0o600)


def enrol(url: str, code: str) -> None:
    answer = httpx.post(
        f"{url.rstrip('/')}/api/v1/agents/enrol",
        json={"code": code, "version": VERSION, "host": socket.gethostname()},
        timeout=20,
    )
    answer.raise_for_status()
    found = answer.json()
    _save({"url": url.rstrip("/"), "token": found["token"], "name": found["name"]})
    print(f"Enrolled as {found['name']}.")


def _shelly(payload: dict[str, Any]) -> dict[str, Any]:
    address, gen2 = payload["address"], int(payload.get("generation") or 2) >= 2
    on = payload.get("on")
    if "on" in payload:
        turn = "on" if on else "off"
        path = f"/rpc/Switch.Set?id=0&on={str(on).lower()}" if gen2 else f"/relay/0?turn={turn}"
    else:
        path = "/rpc/Shelly.GetStatus" if gen2 else "/status"
    answer = httpx.get(f"http://{address}{path}", timeout=5)
    answer.raise_for_status()
    return answer.json()


KNOWN = {"shelly_status": _shelly, "shelly_switch": _shelly}


def run() -> None:
    state = json.loads(STATE.read_text())
    headers = {"Authorization": f"Bearer {state['token']}"}
    with httpx.Client(base_url=state["url"], headers=headers, timeout=30) as api:
        while True:
            try:
                tasks = api.get("/api/v1/agents/tasks", params={"version": VERSION}).json()["tasks"]
                for task in tasks:
                    work = KNOWN.get(task["kind"])
                    try:
                        if work is None:
                            raise ValueError(f"I don't know {task['kind']}.")
                        body = {"ok": True, "result": work(task["payload"] or {})}
                    except (httpx.HTTPError, ValueError, KeyError) as error:
                        body = {"ok": False, "result": {"error": str(error)}}
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
