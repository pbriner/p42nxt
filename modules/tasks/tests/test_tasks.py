"""Tasks on its own installation: priority from impact and urgency, group members taking and
giving back, everyone reading, rules opening and closing tasks on events, watchers and due
dates told."""

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

Acting = Callable[..., AbstractAsyncContextManager[Client]]
RunJob = Callable[[str], Awaitable[tuple[str, str | None]]]
Deliver = Callable[..., Awaitable[list[str]]]


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def _group(client: Client, name: str, *, assignable: bool = True) -> str:
    made = await client.create("sys.group", {"name": f"{name} {_tag()}", "assignable": assignable})
    return str(made["sys_id"])


async def _person(client: Client, name: str, role: str, groups: list[str]) -> str:
    found = await client.list("sys.role", where={"field": "name", "op": "eq", "value": role})
    holding = await _group(client, f"{role} holders", assignable=False)
    await client.create("sys.group_role", {"group": holding, "role": found["records"][0]["sys_id"]})
    person = await client.create("sys.person", {"name": f"{name} {_tag()}"})
    for group in [holding, *groups]:
        await client.create("sys.membership", {"group": group, "person": person["sys_id"]})
    return str(person["sys_id"])


async def _team(client: Client) -> dict[str, str]:
    """Ann and Bob work in the help desk; Cid uses Tasks from another group."""
    desk = await _group(client, "Help desk")
    other = await _group(client, "Garden")
    return {
        "desk": desk,
        "other": other,
        "ann": await _person(client, "Ann", "tasks.user", [desk]),
        "bob": await _person(client, "Bob", "tasks.user", [desk]),
        "cid": await _person(client, "Cid", "tasks.user", [other]),
    }


async def _move(client: Client, task: str, name: str) -> dict[str, Any]:
    await client.action("tasks.task", task, f"tasks.move_{name}")
    return await client.get("tasks.task", task)


async def _watchers(client: Client, task: str) -> set[str]:
    found = await client.list("tasks.watcher", where={"field": "task", "op": "eq", "value": task})
    return {str(w["person"]) for w in found["records"]}


async def _told(client: Client, person: str, kind: str) -> list[str]:
    found = await client.list(
        "sys.notification",
        where={
            "and": [
                {"field": "person", "op": "eq", "value": person},
                {"field": "kind", "op": "eq", "value": kind},
            ]
        },
        limit=50,
    )
    return [n["title"] for n in found["records"]]


@pytest.mark.parametrize(
    ("impact", "urgency", "priority"),
    [("high", "high", "critical"), ("medium", "high", "high"), ("low", "medium", "low")],
)
async def test_priority_comes_from_impact_and_urgency(
    as_system: Acting, impact: str, urgency: str, priority: str
) -> None:
    async with as_system() as client:
        desk = await _group(client, "Desk")
        task = await client.create(
            "tasks.task",
            {"title": "Printer", "group": desk, "impact": impact, "urgency": urgency},
        )
    assert (task["priority"], task["state"], task["number"][:5]) == (priority, "new", "TASK-")


async def test_what_tasks_fills_in_and_the_group_are_checked(as_system: Acting) -> None:
    async with as_system() as client:
        desk = await _group(client, "Desk")
        plain = await _group(client, "Plain", assignable=False)
    for wrong, field in (
        ({"group": desk, "priority": "critical"}, "priority"),
        ({"group": desk, "taken_by": None}, "taken_by"),
        ({"group": plain}, "group"),
    ):
        with pytest.raises(Problem) as refused:
            async with as_system() as client:
                await client.create("tasks.task", {"title": "Printer"} | wrong)
        assert [e.field for e in refused.value.errors] == [field]


async def test_group_members_take_their_work_and_everyone_reads(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        team = await _team(client)
    async with as_person(team["cid"]) as client:  # anyone makes a task for any group
        task = str(
            (await client.create("tasks.task", {"title": "Login fails", "group": team["desk"]}))[
                "sys_id"
            ]
        )
    with pytest.raises(Problem):  # but only the group's members take it
        async with as_person(team["cid"]) as client:
            await _move(client, task, "take")
    async with as_person(team["bob"]) as client:
        taken = await _move(client, task, "take")
        assert (taken["state"], str(taken["taken_by"])) == ("in_progress", team["bob"])
        assert await _watchers(client, task) == {team["cid"], team["bob"]}
    with pytest.raises(Problem):  # only who took it gives it back
        async with as_person(team["ann"]) as client:
            await _move(client, task, "give_back")
    async with as_person(team["cid"]) as client:  # Cid still reads it
        current = await client.get("tasks.task", task)
        assert current["state"] == "in_progress"
    with pytest.raises(Problem):  # but doesn't change it
        async with as_person(team["cid"]) as client:
            await client.update("tasks.task", task, {"title": "Mine now"}, current["_etag"])
    async with as_person(team["bob"]) as client:
        back = await _move(client, task, "give_back")
        assert (back["state"], back["taken_by"]) == ("new", None)
        await _move(client, task, "take")
        done = await _move(client, task, "finish")
        assert done["state"] == "done" and done["closed"] is not None


async def test_moving_a_task_to_another_group_puts_it_there_to_take(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        team = await _team(client)
    async with as_person(team["ann"]) as client:
        task = await client.create("tasks.task", {"title": "Hedge", "group": team["desk"]})
        await _move(client, task["sys_id"], "take")
        current = await client.get("tasks.task", task["sys_id"])
        moved = await client.update(
            "tasks.task", task["sys_id"], {"group": team["other"]}, current["_etag"]
        )
    assert (moved["state"], moved["taken_by"], str(moved["group"])) == ("new", None, team["other"])
    async with as_system() as client:
        assert await _told(client, team["cid"], "tasks.new") == ["New task: Hedge"]


async def test_a_rule_opens_a_task_counts_repeats_and_closes_it(
    as_system: Acting, deliver_events: Deliver
) -> None:
    async with as_system() as client:
        desk = await _group(client, "Operations")
        rule = await client.create(
            "tasks.rule",
            {
                "name": "Failed jobs",
                "event": "job_failed",
                "resolved_by": "job_recovered",
                "title": "Job failed: {job}",
                "group": desk,
                "impact": "high",
                "due_days": 1,
            },
        )
        schedule = (
            await client.list(
                "sys.schedule", where={"field": "name", "op": "eq", "value": "tasks.due"}
            )
        )["records"][0]["sys_id"]
        for _ in range(2):
            await client.publish(
                "sys.job_failed",
                about=("sys.schedule", schedule),
                data={"job": "tasks.due", "message": "Timed out"},
            )
    await deliver_events()
    async with as_system() as client:
        opened = (
            await client.list(
                "tasks.task", where={"field": "rule", "op": "eq", "value": rule["sys_id"]}
            )
        )["records"]
    assert len(opened) == 1
    task = opened[0]
    assert (task["title"], task["priority"], task["repeats"]) == (
        "Job failed: tasks.due",
        "high",
        2,
    )
    assert task["due"] == (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    assert str(task["about"]) == str(schedule)

    async with as_system() as client:
        await client.publish("sys.job_recovered", about=("sys.schedule", schedule), data={})
    await deliver_events()
    async with as_system() as client:
        closed = await client.get("tasks.task", task["sys_id"])
        said = await client.comments("tasks.task", task["sys_id"])
    assert closed["state"] == "done"
    assert [(s["kind"], s["text"]) for s in said] == [
        ("work_note", "Closed by the rule Failed jobs: A scheduled job works again.")
    ]


async def test_watchers_hear_what_is_said(
    as_system: Acting, as_person: Acting, deliver_events: Deliver
) -> None:
    async with as_system() as client:
        team = await _team(client)
    async with as_person(team["ann"]) as client:
        task = await client.create("tasks.task", {"title": "Mail", "group": team["desk"]})
    async with as_person(team["cid"]) as client:
        await client.action("tasks.task", task["sys_id"], "tasks.watch")
    async with as_person(team["bob"]) as client:
        await client.action("tasks.task", task["sys_id"], "tasks.watch")
        await client.comment("tasks.task", task["sys_id"], "Restarted the server.")
        await client.comment(
            "tasks.task", task["sys_id"], "Password in the vault.", kind="work_note"
        )
    await deliver_events()
    async with as_system() as client:
        ann = await _told(client, team["ann"], "tasks.said")
        cid = await _told(client, team["cid"], "tasks.said")
        bob = await _told(client, team["bob"], "tasks.said")
    assert sorted(ann) == [
        "New comment on " + task["number"] + ": Mail",
        "New work note on " + task["number"] + ": Mail",
    ]
    assert cid == ["New comment on " + task["number"] + ": Mail"]  # not the group's: no work notes
    assert bob == []  # who wrote it isn't told


async def test_due_and_overdue_tasks_are_told(as_system: Acting, run_job: RunJob) -> None:
    async with as_system() as client:
        team = await _team(client)
        today = datetime.now(UTC).date()
        await client.create(
            "tasks.task",
            {
                "title": "Soon",
                "group": team["desk"],
                "due": (today + timedelta(days=1)).isoformat(),
            },
        )
        late = await client.create(
            "tasks.task",
            {
                "title": "Late",
                "group": team["desk"],
                "due": (today - timedelta(days=2)).isoformat(),
            },
        )
    state, said = await run_job("tasks.due")
    assert state == "succeeded" and said is not None
    await run_job("tasks.due")  # overdue is told once
    async with as_system() as client:
        told = await _told(client, team["ann"], "tasks.due")
        assert (await client.get("tasks.task", late["sys_id"]))["told"] == today.isoformat()
    assert sorted(told) == ["Due tomorrow: Soon", "Due tomorrow: Soon", "Overdue: Late"]


async def test_who_made_a_task_cancels_it_until_someone_takes_it(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        team = await _team(client)
    async with as_person(team["cid"]) as client:
        first = await client.create("tasks.task", {"title": "Wrong", "group": team["desk"]})
        cancelled = await _move(client, first["sys_id"], "cancel")
        assert cancelled["state"] == "cancelled" and cancelled["closed"] is not None
        second = await client.create("tasks.task", {"title": "Needed", "group": team["desk"]})
    async with as_person(team["ann"]) as client:
        await _move(client, second["sys_id"], "take")
    with pytest.raises(Problem):
        async with as_person(team["cid"]) as client:
            await _move(client, second["sys_id"], "cancel")
