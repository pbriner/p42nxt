"""The index of all configuration items, relationships and the maps.

Every class has its own table; `cmdb.ci` holds one record per CI of any class, kept by hooks on
the class tables (and on administrators' tables registered as classes), so all CIs share one ID
for "All CIs", search, relationships and maps. People don't write the index themselves.
"""

from contextvars import ContextVar
from typing import Any

from p42cloud.sdk import Change, Client, FieldError, Problem, RouteRequest

from p42cloud_cmdb.classes import BY_KEY, COMMON

# Set while the CMDB writes its index itself: people never write it directly.
_OWN: ContextVar[bool] = ContextVar("cmdb_own", default=False)
INDEXED = ("name", "status", "criticality", "owner", "place", "last_seen")
MAX_DEPTH = 6
MAX_NODES = 120


def invalid(field: str, message: str) -> Problem:
    errors = [FieldError(field, "invalid", message)]
    return Problem(422, "validation", "Some fields aren't valid.", errors=errors)


def _eq(field: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": "eq", "value": str(value)}


async def every(client: Client, table: str, where: Any = None) -> list[dict[str, Any]]:
    """All records of a table matching, page by page."""
    found: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await client.list(table, where=where, limit=500, after=after)
        found += page["records"]
        after = page["next"]
        if after is None:
            return found


async def entry(client: Client, table: str, record: Any, *, deleted: bool = False) -> Any:
    """The index record of a class record, if any."""
    found = await client.list(
        "cmdb.ci",
        where={"and": [_eq("table", table), _eq("record", record)]},
        limit=1,
        deleted=deleted,
    )
    return found["records"][0] if found["records"] else None


async def _class_of(client: Client, table: str) -> tuple[str, str | None]:
    key = table.split(".", 1)[1] if table.startswith("cmdb.") else table
    if key in BY_KEY:
        return key, BY_KEY[key].family
    registered = await client.list("cmdb.ci_class", where=_eq("table", table), limit=1)
    if not registered["records"]:
        return "", None
    row = registered["records"][0]
    return str(row["name"]), row.get("family")


async def sync(client: Client, table: str, record: Any, operation: str) -> None:
    """Brings the index record of one class record to it: made, changed, deleted, restored."""
    token = _OWN.set(True)
    try:
        if operation == "delete":
            found = await entry(client, table, record)
            if found is not None:
                await client.delete("cmdb.ci", found["sys_id"], found["_etag"])
            return
        if operation == "restore":
            found = await entry(client, table, record, deleted=True)
            if found is not None:
                await client.restore("cmdb.ci", found["sys_id"])
            return
        values = await client.get(table, record)
        key, family = await _class_of(client, table)
        if not key:
            return
        wanted = {k: values.get(k) for k in INDEXED if k in values} | {
            "ci_class": key,
            "family": family,
        }
        found = await entry(client, table, record)
        if found is None:
            await client.create("cmdb.ci", wanted | {"table": table, "record": str(record)})
        elif any(str(found.get(k)) != str(v) for k, v in wanted.items()):
            await client.update("cmdb.ci", found["sys_id"], wanted, found["_etag"])
    finally:
        _OWN.reset(token)


async def index_after(change: Change) -> None:
    """After a class record changes: its index record follows."""
    if change.sys_id is not None:
        await sync(Client.of(change), change.table.key, change.sys_id, change.operation)


async def custom_after(change: Change) -> None:
    """Administrators' own tables (schema u) registered as classes are indexed too."""
    if (
        change.table.schema != "u"
        or change.sys_id is None
        or change.records.catalog.table("cmdb.ci_class") is None  # the CMDB isn't installed here
    ):
        return
    client = Client.of(change)
    registered = await client.list("cmdb.ci_class", where=_eq("table", change.table.key), limit=1)
    if registered["records"]:
        await sync(client, change.table.key, change.sys_id, change.operation)


async def check_index(change: Change) -> None:
    if not _OWN.get() and not change.records.access.unrestricted:
        raise invalid("ci_class", "The index follows the classes: change the item itself.")


async def check_class(change: Change) -> None:
    """A custom class is an administrator's own table with a name field; shipped ones stay."""
    if change.records.access.unrestricted or change.operation in ("restore",):
        return
    client = Client.of(change)
    current = await client.get("cmdb.ci_class", change.sys_id) if change.sys_id else {}
    if current and not current.get("custom"):
        raise invalid("table", "A shipped class stays as it is.")
    if change.operation == "delete":
        return
    merged = current | change.values
    table = str(merged.get("table") or "")
    if not table.startswith("u."):
        raise invalid("table", "A class of your own is one of your own tables (u.<name>).")
    try:
        await client.list(table, limit=1)
    except Problem as problem:
        raise invalid("table", f"There's no table {table} you may read.") from problem
    change.values["custom"] = True


# ---- relationships ----


async def check_type(change: Change) -> None:
    """Shipped types a manager changes are kept through updates; new ones are custom."""
    if change.records.access.unrestricted:
        return
    if change.operation == "create":
        change.values["custom"] = True
    elif change.operation == "write":
        change.values["changed"] = True


async def _reaches(client: Client, start: Any, goal: Any, types: set[str]) -> bool:
    """Whether following `needs` relationships from start leads to goal (a loop)."""
    seen: set[str] = set()
    waiting = [str(start)]
    while waiting and len(seen) < MAX_NODES * 4:
        here = waiting.pop()
        if here == str(goal):
            return True
        if here in seen:
            continue
        seen.add(here)
        for r in await every(client, "cmdb.relationship", _eq("from_ci", here)):
            if str(r["type"]) in types:
                waiting.append(str(r["to_ci"]))
    return False


async def check_relationship(change: Change) -> None:
    """Not to itself, between the classes its type allows, no loops of needs, once per pair."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    current = await client.get("cmdb.relationship", change.sys_id) if change.sys_id else {}
    merged = current | change.values
    a, b = str(merged.get("from_ci")), str(merged.get("to_ci"))
    if a == b:
        raise invalid("to_ci", "A relationship links two different items.")
    kind = await client.get("cmdb.relation_type", str(merged["type"]))
    for side, allowed_field, field in ((a, "from_classes", "from_ci"), (b, "to_classes", "to_ci")):
        allowed: list[str] = kind.get(allowed_field) or []
        if allowed:
            ci = await client.get("cmdb.ci", side)
            if ci["ci_class"] not in allowed:
                raise invalid(field, f"{kind['label']} doesn't link a {ci['ci_class']} this way.")
    if kind.get("both_ways"):
        reverse = await client.list(
            "cmdb.relationship",
            where={"and": [_eq("from_ci", b), _eq("to_ci", a), _eq("type", merged["type"])]},
            limit=1,
        )
        if reverse["records"]:
            raise invalid("to_ci", "They're linked like this already (it reads both ways).")
    if kind["meaning"] == "needs":
        needs = {
            str(t["sys_id"])
            for t in await every(client, "cmdb.relation_type", _eq("meaning", "needs"))
        }
        if await _reaches(client, b, a, needs):
            raise invalid("to_ci", "That would make a loop: the other one needs this one already.")


# ---- maps ----


def _tone(status: Any) -> str | None:
    return {"in_repair": "bad", "retired": "muted", "planned": "muted"}.get(str(status))


async def _walk(  # noqa: PLR0913 - one direction of the map, all it needs
    client: Client,
    types: dict[str, Any],
    root: str,
    *,
    depth: int,
    sign: int,
    needs_only: bool,
    levels: dict[str, int],
    edges: dict[tuple[str, str, str], str],
) -> None:
    near, far = ("from_ci", "to_ci") if sign > 0 else ("to_ci", "from_ci")
    frontier = [root]
    for level in range(1, depth + 1):
        following: list[str] = []
        for here in frontier:
            for r in await every(client, "cmdb.relationship", _eq(near, here)):
                kind = types.get(str(r["type"]))
                if kind is None or (needs_only and kind["meaning"] != "needs"):
                    continue
                other = str(r[far])
                label = kind["label"] if sign > 0 else kind["reverse_label"]
                edges[(here, other, label)] = label
                if other not in levels and len(levels) < MAX_NODES:
                    levels[other] = level * sign
                    if kind["meaning"] == "needs":
                        following.append(other)
        frontier = following


async def graph(
    client: Client, root: str, direction: str = "both", depth: int = 3, needs_only: bool = False
) -> dict[str, Any]:
    """The items linked to one, level by level: what it needs to the right (positive levels),
    what needs it to the left (negative). Only what the caller may read; cycles stop."""
    depth = max(1, min(MAX_DEPTH, depth))
    types = {str(t["sys_id"]): t for t in await every(client, "cmdb.relation_type")}
    levels: dict[str, int] = {root: 0}
    edges: dict[tuple[str, str, str], str] = {}
    if direction in ("down", "both"):
        await _walk(
            client,
            types,
            root,
            depth=depth,
            sign=1,
            needs_only=needs_only,
            levels=levels,
            edges=edges,
        )
    if direction in ("up", "both"):
        await _walk(
            client,
            types,
            root,
            depth=depth,
            sign=-1,
            needs_only=needs_only,
            levels=levels,
            edges=edges,
        )
    nodes: list[dict[str, Any]] = []
    for ci_id, level in levels.items():
        try:
            ci = await client.get("cmdb.ci", ci_id)
        except Problem:
            continue  # one they may not read stays off their map
        nodes.append(
            {
                "id": ci_id,
                "label": ci["name"],
                "kind": ci.get("_display", {}).get("ci_class") or ci["ci_class"],
                "status": ci.get("_display", {}).get("status") or ci.get("status"),
                "level": level,
                "link": f"/form/{ci['table']}/{ci['record']}",
                "tone": _tone(ci.get("status")),
            }
        )
    shown = {n["id"] for n in nodes}
    return {
        "nodes": nodes,
        "edges": [
            {"from": a, "to": b, "label": label}
            for (a, b, label) in edges
            if a in shown and b in shown
        ],
    }


async def map_route(request: RouteRequest) -> dict[str, Any]:
    root = request.query.get("ci")
    if not root:
        raise Problem(422, "validation", "Pick an item.")
    direction = request.query.get("direction", "both")
    if direction not in ("down", "up", "both"):
        raise Problem(422, "validation", "down, up or both.")
    depth = int(request.query.get("depth", "3") or 3)
    return await graph(Client.of(request), root, direction, depth)


async def impact_route(request: RouteRequest) -> dict[str, Any]:
    """If this is down: everything that needs it, directly or not, nearest first."""
    root = request.query.get("ci")
    if not root:
        raise Problem(422, "validation", "Pick an item.")
    found = await graph(Client.of(request), root, "up", MAX_DEPTH, needs_only=True)
    affected = sorted((n for n in found["nodes"] if n["level"] < 0), key=lambda n: -n["level"])
    return {"affected": affected}


__all__ = ["COMMON"]
