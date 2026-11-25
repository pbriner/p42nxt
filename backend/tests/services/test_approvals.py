"""Approvals: a move that needs others' yes, in steps (one only above an amount), never by who
asked, with deputies, rejections with a reason, the record held while it waits, reminders and
escalation, and rules administrators change kept through updates."""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.decimal import Decimal
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.access.roles import Grant, Role
from p42cloud.services.approvals.models import Approval, Step
from p42cloud.services.approvals.service import decide, follow_up, of_record, withdraw
from p42cloud.services.jobs.service import run_job
from p42cloud.services.states.models import Flow, Transition
from p42cloud.ui.actions.service import run
from tests.security.helpers import acting, role_id

ALL = ("read", "create", "write", "delete", "restore")


def _module(m: str) -> Module:
    return Module(
        id=m,
        publisher="test",
        name="Requests",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(
            Table(
                "request",
                "Request",
                {
                    "title": String("Title", max_length=60),
                    "amount": Decimal("Amount"),
                    "state": Choice("State", {"draft": "Draft", "approved": "Approved"}),
                },
                display="title",
            ),
        ),
        roles=(
            Role(f"{m}.clerk", "Clerk", (Grant(f"{m}.request", ALL),)),
            Role(f"{m}.boss", "Boss", (Grant(f"{m}.request", ("read",)),)),
            Role(f"{m}.ceo", "CEO", (Grant(f"{m}.request", ("read",)),)),
        ),
        flows=(
            Flow(
                f"{m}.request",
                "state",
                initial="draft",
                transitions=(
                    Transition(
                        "approve",
                        "Approve",
                        "draft",
                        "approved",
                        approval=Approval(
                            steps=(Step(f"{m}.boss"), Step(f"{m}.ceo", "amount", "1000")),
                            remind_after=2,
                            escalate_after=5,
                        ),
                    ),
                ),
            ),
        ),
    )


@pytest.fixture(scope="module")
async def app_engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def office(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]]
) -> dict[str, Any]:
    """Ann is a clerk and a boss; Bob a boss; Cid the CEO; Dee stands in for Bob."""
    m = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(m)])
    people: dict[str, Any] = {"m": m}
    async with acting(app_engine, None) as records:
        for name, roles in (("ann", ("clerk", "boss")), ("bob", ("boss",)), ("cid", ("ceo",))):
            group = await records.create("sys.group", {"name": f"{name} {uuid.uuid4().hex[:6]}"})
            for role in roles:
                await records.create(
                    "sys.group_role",
                    {"group": group.sys_id, "role": await role_id(records, f"{m}.{role}")},
                )
            person = await records.create("sys.person", {"name": f"{name.title()} {m}"})
            await records.create("sys.membership", {"group": group.sys_id, "person": person.sys_id})
            people[name] = person.sys_id
        people["dee"] = (await records.create("sys.person", {"name": f"Dee {m}"})).sys_id
    return people


async def _request(engine: AsyncEngine, office: dict[str, Any], amount: str) -> uuid.UUID:
    async with acting(engine, office["ann"]) as records:
        made = await records.create(f"{office['m']}.request", {"title": "Laptop", "amount": amount})
    return made.sys_id


async def _ask(engine: AsyncEngine, office: dict[str, Any], record: uuid.UUID) -> str:
    async with acting(engine, office["ann"]) as records:
        done = await run(
            records, f"{office['m']}.request", f"{office['m']}.move_approve", record, given={}
        )
    return str(done["message"])


async def _state(engine: AsyncEngine, office: dict[str, Any], record: uuid.UUID) -> str:
    async with acting(engine, None) as records:
        return str((await records.read(f"{office['m']}.request", record)).values["state"])


async def _approvals(
    engine: AsyncEngine, who: uuid.UUID, office: dict[str, Any], record: uuid.UUID
) -> list[dict[str, Any]]:
    async with acting(engine, who) as records:
        table = records.catalog.tables[f"{office['m']}.request"]
        return await of_record(records, table, record)


async def test_a_move_waits_for_approval_and_who_asked_never_approves(
    app_engine: AsyncEngine, office: dict[str, Any]
) -> None:
    key = f"{office['m']}.request"
    small = await _request(app_engine, office, "300")
    with pytest.raises(Problem) as around:  # the move itself isn't made around the approval
        async with acting(app_engine, office["ann"]) as records:
            current = await records.read(key, small)
            await records.update(key, small, {"state": "approved"}, current.etag)
    assert around.value.code == "needs_approval"
    assert await _ask(app_engine, office, small) == "Asked for approval."
    assert await _state(app_engine, office, small) == "draft"
    with pytest.raises(Problem) as held:  # waiting: it doesn't change
        async with acting(app_engine, office["ann"]) as records:
            current = await records.read(key, small)
            await records.update(key, small, {"title": "Two laptops"}, current.etag)
    assert held.value.code == "pending"
    shown = (await _approvals(app_engine, office["ann"], office, small))[0]
    assert [a["name"] for a in shown["approvers"]] == [f"Bob {office['m']}"]  # not Ann herself
    assert shown["steps"] == ["Boss"]  # the CEO's step is only above 1000
    assert (shown["may_decide"], shown["may_withdraw"]) == (False, True)
    async with acting(app_engine, office["bob"]) as records:
        with pytest.raises(Problem):
            await decide(records, uuid.UUID(shown["sys_id"]), "reject", " ")  # a no says why
        said = await decide(records, uuid.UUID(shown["sys_id"]), "approve", None)
    assert said == {"state": "approved"}
    assert await _state(app_engine, office, small) == "approved"


async def test_steps_above_an_amount_deputies_and_a_no(
    app_engine: AsyncEngine, office: dict[str, Any]
) -> None:
    today = datetime.now(UTC).date()
    async with acting(app_engine, office["bob"]) as records:  # Bob is away; Dee stands in
        await records.create(
            "sys.deputy",
            {
                "deputy": office["dee"],
                "starts": today.isoformat(),
                "ends": (today + timedelta(days=7)).isoformat(),
            },
        )
    big = await _request(app_engine, office, "5000")
    await _ask(app_engine, office, big)
    approval = uuid.UUID((await _approvals(app_engine, office["ann"], office, big))[0]["sys_id"])
    async with acting(app_engine, office["dee"]) as records:
        assert await decide(records, approval, "approve", "For Bob.") == {
            "state": "pending",
            "step": 1,
        }
    async with acting(app_engine, office["cid"]) as records:
        assert await decide(records, approval, "reject", "Too expensive.") == {"state": "rejected"}
    shown = (await _approvals(app_engine, office["ann"], office, big))[0]
    assert shown["state"] == "rejected" and shown["note"] == "Too expensive."
    assert next((d["name"], d["for"]) for d in shown["decisions"]) == (
        f"Dee {office['m']}",
        f"Bob {office['m']}",
    )
    assert await _state(app_engine, office, big) == "draft"
    async with acting(app_engine, None) as records:
        told = await records.conn.scalar(
            text("SELECT title FROM sys.notification WHERE person = :p AND kind = :k"),
            {"p": office["ann"], "k": "sys.approval_decided"},
        )
    assert told.startswith("Rejected: Request Laptop")


async def test_withdrawn_reminded_escalated(
    app_engine: AsyncEngine, office: dict[str, Any]
) -> None:
    first = await _request(app_engine, office, "10")
    await _ask(app_engine, office, first)
    approval = uuid.UUID((await _approvals(app_engine, office["ann"], office, first))[0]["sys_id"])
    async with acting(app_engine, office["ann"]) as records:
        assert await withdraw(records, approval) == {"state": "withdrawn"}
    second = await _request(app_engine, office, "10")
    await _ask(app_engine, office, second)
    waiting = uuid.UUID((await _approvals(app_engine, office["ann"], office, second))[0]["sys_id"])
    async with acting(app_engine, None) as records:
        root = await records.create("sys.person", {"name": f"Root {office['m']}"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": root.sys_id})
        back = "UPDATE sys.approval SET step_started = :d WHERE sys_id = :a"
        await records.conn.execute(
            text(back),
            {"a": waiting, "d": datetime.now(UTC) - timedelta(days=3)},
        )
        assert await follow_up(records.conn, datetime.now(UTC)) == (1, 0)  # reminded
        await records.conn.execute(
            text(back), {"a": waiting, "d": datetime.now(UTC) - timedelta(days=6)}
        )
        reminded, escalated = await follow_up(records.conn, datetime.now(UTC))
        admins = await records.conn.scalar(
            text("SELECT count(*) FROM sys.approver WHERE approval = :a AND escalation"),
            {"a": waiting},
        )
    assert (reminded, escalated) == (0, 1)
    assert admins >= 1  # the administrators got it
    state, _ = await run_job(app_engine, "sys.approval_follow_up")
    assert state == "succeeded"


async def test_a_rule_an_administrator_changed_is_kept(
    owner_engine: AsyncEngine, app_engine: AsyncEngine, office: dict[str, Any]
) -> None:
    async with acting(app_engine, None) as records:
        admin = await records.create("sys.person", {"name": f"Root {office['m']}"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin.sys_id})
    name = f"{office['m']}.approve"
    async with acting(app_engine, admin.sys_id) as records:
        rule = (
            await records.list(
                "sys.approval_rule", where={"field": "name", "op": "eq", "value": name}
            )
        ).records[0]
        await records.update(
            "sys.approval_rule", rule.sys_id, {"steps": [{"role": f"{office['m']}.ceo"}]}, rule.etag
        )
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(office["m"])])
    async with acting(app_engine, None) as records:
        kept = (
            await records.list(
                "sys.approval_rule", where={"field": "name", "op": "eq", "value": name}
            )
        ).records[0]
    assert kept.values["steps"] == [{"role": f"{office['m']}.ceo"}]
    assert kept.values["changed"] is True
