"""What Tasks does beyond storing records: priority from impact and urgency, taking and giving
back, watchers, notices, and rules that turn events into tasks and close them again."""

import re
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from typing import Any

from p42cloud.sdk import (
    ActionResult,
    ActionRun,
    Change,
    Client,
    EventRun,
    FieldError,
    JobRun,
    Problem,
)

LEVELS = {"high": "High", "medium": "Medium", "low": "Low"}
PRIORITIES = {"critical": "Critical", "high": "High", "moderate": "Moderate", "low": "Low"}
# How big the effect is (impact) and how soon it must be solved (urgency) give the priority.
#               urgency: high        medium      low
MATRIX = {
    "high":   {"high": "critical", "medium": "high",     "low": "moderate"},
    "medium": {"high": "high",     "medium": "moderate", "low": "low"},
    "low":    {"high": "moderate", "medium": "low",      "low": "low"},
}  # fmt: skip
STATES = {
    "new": "New",
    "in_progress": "In progress",
    "waiting": "Waiting",
    "done": "Done",
    "cancelled": "Cancelled",
}
OPEN = ("new", "in_progress", "waiting")
CLOSED = ("done", "cancelled")
TAKEN = ("in_progress", "waiting")
# The events Tasks listens for, as a rule names them: what opens a task, what closes it.
EVENTS = {
    "job_failed": ("sys.job_failed", "A scheduled job fails"),
    "job_recovered": ("sys.job_recovered", "A scheduled job works again"),
}
EVENT_CHOICES = {key: label for key, (_, label) in EVENTS.items()}
BY_TYPE = {event: key for key, (event, _) in EVENTS.items()}
# Fields only Tasks itself fills; nobody sends them.
OWN_FIELDS = frozenset({"priority", "taken_by", "closed", "rule", "repeats", "last_seen", "told"})
MAX_TITLE = 200
PAGE = 500  # the most the client gives at once

# Set while Tasks' own rules and jobs write: the fields people may not send.
_OWN: ContextVar[bool] = ContextVar("tasks_own_writes", default=False)


async def every(client: Client, table: str, where: Any) -> list[dict[str, Any]]:
    """Every record of a list, page by page."""
    found: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await client.list(table, where=where, limit=PAGE, after=after)
        found += page["records"]
        after = page["next"]
        if not after:
            return found


def priority_of(impact: str, urgency: str) -> str:
    return MATRIX[impact][urgency]


def invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _assignable(client: Client, group: Any) -> None:
    found = await client.get("sys.group", group)
    if not found.get("assignable") or found.get("deleted"):
        raise invalid("group", "Choose a group work is assigned to.")


async def check_task(change: Change) -> None:
    """Before a task is saved: the fields only Tasks fills, the group, the priority, who took it
    and when it closed."""
    if change.operation in ("delete", "restore"):
        return
    values = change.values
    if not _OWN.get() and (sent := sorted(OWN_FIELDS & change.sent)):
        raise invalid(sent[0], "This is filled in by Tasks.")
    client = Client.of(change)
    current = await client.get("tasks.task", change.sys_id) if change.sys_id is not None else {}
    if "group" in values and values["group"] != current.get("group"):
        await _assignable(client, values["group"])
        if current.get("state") in TAKEN:
            # Another group's work now: it waits there to be taken again.
            values["state"] = "new"
    impact = values.get("impact", current.get("impact")) or "medium"
    urgency = values.get("urgency", current.get("urgency")) or "medium"
    values["priority"] = priority_of(impact, urgency)
    state = values.get("state", current.get("state"))
    before = current.get("state")
    if state != before or change.operation == "create":
        if state in TAKEN and before not in TAKEN:
            values["taken_by"] = str(change.person)
        elif state == "new":
            values["taken_by"] = None
        if state in CLOSED:
            values["closed"] = datetime.now(UTC).isoformat()
        elif before in CLOSED:
            values["closed"] = None


async def watch_and_tell(change: Change) -> None:
    """After a task is saved: the creator and whoever takes it watch it; a new task (or one moved
    to another group) is told to its group."""
    if change.operation not in {"create", "write"}:
        return
    client = Client.of(change)
    task = await client.get("tasks.task", change.sys_id or change.values["sys_id"])
    if change.operation == "create" and not _OWN.get():
        await _watch(client, task["sys_id"], change.person)
    if change.operation == "write" and task["taken_by"] and "state" in change.values:
        await _watch(client, task["sys_id"], task["taken_by"])
    moved = change.operation == "write" and "group" in change.sent
    if change.operation == "create" or (moved and task["state"] == "new"):
        await client.notify(
            "tasks.new",
            f"New task: {task['title']}",
            groups=[task["group"]],
            message=f"{task['number']} · {PRIORITIES[task['priority']]}",
            link=f"/form/tasks.task/{task['sys_id']}",
        )


async def _watch(client: Client, task: Any, person: Any) -> None:
    found = await client.list(
        "tasks.watcher",
        where={
            "and": [
                {"field": "task", "op": "eq", "value": str(task)},
                {"field": "person", "op": "eq", "value": str(person)},
            ]
        },
    )
    if not found["records"]:
        await client.create("tasks.watcher", {"task": str(task), "person": str(person)})


async def check_rule(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    values = change.values
    if "group" in values:
        await _assignable(Client.of(change), values["group"])
    days = values.get("due_days")
    if days is not None and not 0 <= int(days) <= 365:  # noqa: PLR2004
        raise invalid("due_days", "From 0 to 365 days.")
    if values.get("event") and values.get("event") == values.get("resolved_by"):
        raise invalid("resolved_by", "Another event than the one that opens it.")


async def take_watch(run: ActionRun) -> ActionResult:
    client = Client(run.records)
    await _watch(client, run.sys_id, run.person)
    return ActionResult("You watch this task: you hear of what's said and when it's due.")


async def stop_watching(run: ActionRun) -> ActionResult:
    client = Client(run.records)
    found = await client.list(
        "tasks.watcher",
        where={
            "and": [
                {"field": "task", "op": "eq", "value": str(run.sys_id)},
                {"field": "person", "op": "eq", "value": str(run.person)},
            ]
        },
    )
    for watcher in found["records"]:
        await client.delete("tasks.watcher", watcher["sys_id"], watcher["_etag"])
    return ActionResult("You no longer watch this task.")


def fill(template: str, data: dict[str, Any]) -> str:
    """A rule's title with the event's data put in: `{message}` becomes the data's message;
    unknown names stay as written."""
    said = re.sub(r"\{(\w+)\}", lambda m: str(data.get(m[1], m[0])), template)
    return said[:MAX_TITLE]


async def on_event(run: EventRun) -> None:
    """An event Tasks listens for: rules opening on it open a task (or count a repeat on the
    one still open); rules closing on it close their open task about the same record."""
    key = BY_TYPE.get(run.type)
    if key is None or run.about_table is None or run.about_id is None:
        return
    client = Client(run.records)
    token = _OWN.set(True)
    try:
        opening = await _rules(client, "event", key)
        for rule in opening:
            await _open(client, rule, run)
        for rule in await _rules(client, "resolved_by", key):
            await _close(client, rule, run)
    finally:
        _OWN.reset(token)


async def _rules(client: Client, field: str, key: str) -> list[dict[str, Any]]:
    found = await client.list(
        "tasks.rule",
        where={
            "and": [
                {"field": field, "op": "eq", "value": key},
                {"field": "active", "op": "eq", "value": True},
            ]
        },
        limit=PAGE,
    )
    return found["records"]


async def _open_task(client: Client, rule: dict[str, Any], run: EventRun) -> dict[str, Any] | None:
    found = await client.list(
        "tasks.task",
        where={
            "and": [
                {"field": "rule", "op": "eq", "value": rule["sys_id"]},
                {"field": "about", "op": "eq", "value": str(run.about_id)},
                {"field": "state", "op": "in", "value": list(OPEN)},
            ]
        },
        limit=1,
    )
    return found["records"][0] if found["records"] else None


async def _open(client: Client, rule: dict[str, Any], run: EventRun) -> None:
    now = run.published.astimezone(UTC).isoformat()
    task = await _open_task(client, rule, run)
    if task is not None:  # it happened again: counted on the task still open
        await client.update(
            "tasks.task",
            task["sys_id"],
            {"repeats": int(task["repeats"]) + 1, "last_seen": now},
            task["_etag"],
        )
        return
    due = (
        (run.published.astimezone(UTC).date() + timedelta(days=int(rule["due_days"]))).isoformat()
        if rule["due_days"] is not None
        else None
    )
    await client.create(
        "tasks.task",
        {
            "title": fill(rule["title"], run.data) or rule["name"],
            "description": "\n".join(f"{k}: {v}" for k, v in run.data.items()) or None,
            "impact": rule["impact"],
            "urgency": rule["urgency"],
            "group": rule["group"],
            "due": due,
            "about_table": run.about_table,
            "about": str(run.about_id),
            "rule": rule["sys_id"],
            "repeats": 1,
            "last_seen": now,
        },
    )


async def _close(client: Client, rule: dict[str, Any], run: EventRun) -> None:
    task = await _open_task(client, rule, run)
    if task is None:
        return
    _, label = EVENTS[BY_TYPE[run.type]]
    await client.comment(
        "tasks.task",
        task["sys_id"],
        f"Closed by the rule {rule['name']}: {label}.",
        kind="work_note",
    )
    current = await client.get("tasks.task", task["sys_id"])
    await client.update("tasks.task", task["sys_id"], {"state": "done"}, current["_etag"])


async def on_said(run: EventRun) -> None:
    """A comment or a work note on a task: its watchers hear of it (not who wrote it). A work
    note only reaches watchers in the task's group: nobody else reads it."""
    if run.about_table != "tasks.task" or run.about_id is None:
        return
    client = Client(run.records)
    task = await client.get("tasks.task", run.about_id)
    watchers = await every(
        client, "tasks.watcher", {"field": "task", "op": "eq", "value": str(run.about_id)}
    )
    people = {str(w["person"]) for w in watchers} - {str(run.published_by)}
    note = run.type == "sys.work_note_added"
    if note:
        members = await every(
            client, "sys.membership", {"field": "group", "op": "eq", "value": str(task["group"])}
        )
        people &= {str(m["person"]) for m in members}
    if not people:
        return
    what = "work note" if note else "comment"
    await client.notify(
        "tasks.said",
        f"New {what} on {task['number']}: {task['title']}"[:MAX_TITLE],
        people=sorted(people),
        link=f"/form/tasks.task/{task['sys_id']}",
    )


async def tell_due(run: JobRun) -> str:
    """Daily: open tasks due tomorrow, and overdue ones once, are told to whoever took them, or
    to their group while nobody has."""
    client = Client(run.records)
    today = datetime.now(UTC).date()
    token = _OWN.set(True)
    try:
        soon = await _due(
            client, {"field": "due", "op": "eq", "value": (today + timedelta(days=1)).isoformat()}
        )
        late = await _due(
            client,
            {
                "and": [
                    {"field": "due", "op": "lt", "value": today.isoformat()},
                    {"field": "told", "op": "empty"},
                ]
            },
        )
        for task in soon:
            await _tell(client, task, "Due tomorrow")
        for task in late:
            await _tell(client, task, "Overdue")
            await client.update(
                "tasks.task", task["sys_id"], {"told": today.isoformat()}, task["_etag"]
            )
    finally:
        _OWN.reset(token)
    return f"{len(soon)} due tomorrow, {len(late)} overdue told."


async def _due(client: Client, when: dict[str, Any]) -> list[dict[str, Any]]:
    return await every(
        client, "tasks.task", {"and": [when, {"field": "state", "op": "in", "value": list(OPEN)}]}
    )


async def _tell(client: Client, task: dict[str, Any], what: str) -> None:
    to = {"people": [task["taken_by"]]} if task["taken_by"] else {"groups": [task["group"]]}
    await client.notify(
        "tasks.due",
        f"{what}: {task['title']}"[:MAX_TITLE],
        message=f"{task['number']} · due {date.fromisoformat(task['due']):%d.%m.%Y}",
        link=f"/form/tasks.task/{task['sys_id']}",
        severity="warning" if what == "Overdue" else "info",
        **to,
    )
