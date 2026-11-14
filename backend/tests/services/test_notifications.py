"""Notifications: sent with a change, to people and the roles' holders, seen only by their own."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.services.jobs import service as jobs
from p42cloud.services.jobs.models import Job, JobRun
from p42cloud.services.notifications.service import KINDS, clean_up, send
from tests.security.helpers import acting, change

KIND = "sys.test_kind"


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    KINDS[KIND] = "sys"
    KINDS.setdefault("sys.job_failed", "sys")
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()
    KINDS.pop(KIND, None)


async def _person(engine: AsyncEngine, name: str, **values: Any) -> uuid.UUID:
    async with acting(engine, None) as records:
        made = await records.create(
            "sys.person", {"name": f"{name} {uuid.uuid4().hex[:6]}", **values}
        )
    return made.sys_id


async def _admin(engine: AsyncEngine) -> uuid.UUID:
    person = await _person(engine, "Admin")
    async with acting(engine, None) as records:
        await records.create(
            "sys.membership", {"person": str(person), "group": str(ADMINISTRATORS)}
        )
    return person


async def _inbox(engine: AsyncEngine, person: uuid.UUID) -> list[dict[str, Any]]:
    async with acting(engine, person) as records:
        page = await records.list("sys.notification", sort=["-created"], limit=500)
    return [dict(r.values) | {"sys_id": r.sys_id} for r in page.records]


async def _send(records: Records, **given: Any) -> int:
    return await send(records.conn, KIND, given.pop("title", "Hello"), **given)


async def test_people_and_the_roles_holders_are_notified(engine: AsyncEngine) -> None:
    anna = await _person(engine, "Anna")
    gone = await _person(engine, "Gone", active=False)
    robot = await _person(engine, "Robot", kind="service")
    admin = await _admin(engine)
    with pytest.raises(RuntimeError):
        async with acting(engine, None) as records:
            await _send(records, title="Undone", people=[anna])
            raise RuntimeError("the change is undone")
    async with acting(engine, None) as records:
        sent = await _send(
            records,
            title="Your order went through",
            people=[anna, gone, robot],
            roles=["sys.admin"],
            message="50 CHF",
            link="/form/sys.person/new",
            severity="success",
        )
    mine = await _inbox(engine, anna)
    assert sent >= 2  # Anna and every administrator, the new one among them
    assert [n["title"] for n in mine] == ["Your order went through"]
    assert mine[0]["read"] is False and mine[0]["severity"] == "success"
    assert any(n["title"] == "Your order went through" for n in await _inbox(engine, admin))
    async with acting(engine, None) as records:
        for_robot = await records.conn.scalar(
            text("SELECT count(*) FROM sys.notification WHERE person = ANY(:p)"),
            {"p": [gone, robot]},
        )
    assert for_robot == 0


async def test_only_the_person_sees_and_marks_their_notifications(engine: AsyncEngine) -> None:
    anna = await _person(engine, "Anna")
    admin = await _admin(engine)
    async with acting(engine, None) as records:
        await _send(records, title="For Anna only", people=[anna])
    ident = (await _inbox(engine, anna))[0]["sys_id"]
    assert all(n["title"] != "For Anna only" for n in await _inbox(engine, admin))
    async with acting(engine, admin) as records:
        with pytest.raises(Problem):
            await records.read("sys.notification", ident)
    async with acting(engine, anna) as records:
        await change(records, "sys.notification", ident, {"read": True})
        with pytest.raises(Problem) as retitled:
            await change(records, "sys.notification", ident, {"title": "Something else"})
        with pytest.raises(Problem) as made:
            await records.create(
                "sys.notification", {"person": str(anna), "kind": KIND, "title": "Mine"}
            )
    assert (await _inbox(engine, anna))[0]["read"] is True
    assert retitled.value.status == made.value.status == 403


@pytest.mark.parametrize(
    "link", ["https://evil.test/", "//evil.test/", "/\\evil.test", "javascript:alert(1)", "/a\nb"]
)
async def test_notifications_link_only_inside_the_app(engine: AsyncEngine, link: str) -> None:
    anna = await _person(engine, "Anna")
    async with acting(engine, None) as records:
        with pytest.raises(Problem) as refused:
            await _send(records, people=[anna], link=link)
    assert refused.value.status == 422


async def test_unknown_kinds_and_bad_severities_are_refused(engine: AsyncEngine) -> None:
    anna = await _person(engine, "Anna")
    async with acting(engine, None) as records:
        with pytest.raises(Problem) as unknown:
            await send(records.conn, "sys.nothing", "Hi", people=[anna])
        with pytest.raises(Problem) as severity:
            await _send(records, people=[anna], severity="panic")
        nobody = await _send(records)
    assert (unknown.value.status, severity.value.status, nobody) == (404, 422, 0)


async def test_administrators_hear_once_when_a_schedule_starts_failing(
    engine: AsyncEngine,
) -> None:
    admin = await _admin(engine)
    outcome = {"fail": True}

    async def flaky(run: JobRun) -> str:
        if outcome["fail"]:
            raise ValueError("the outside system said no")
        return "Fine."

    name = f"flaky{uuid.uuid4().hex[:6]}"
    full = f"sys.{name}"
    with working_as(Context(channel="test", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "INSERT INTO sys.job (name, module, label, timeout, retries, custom)"
                    " VALUES (:n, 'sys', '{\"en\": \"Flaky\"}', 60, 0, false);"
                ),
                {"n": full},
            )
            await conn.execute(
                text(
                    "INSERT INTO sys.schedule (name, module, label, job, cron, time_zone, missed,"
                    " active, custom) VALUES (:n, 'sys', '{\"en\": \"Flaky nightly\"}', :n,"
                    " '0 3 * * *', 'UTC', 'skip', true, false)"
                ),
                {"n": full},
            )
    jobs.JOBS[full] = ("sys", Job(name, "Flaky", flaky, retries=0))

    async def run_once() -> None:
        with working_as(Context(channel="test", access=EVERYTHING)):
            async with transaction(engine, "sys") as conn:
                await jobs.queue(conn, full, full, "manual")
        while (run := await jobs.claim(engine, "test")) is not None:
            await jobs.execute(engine, run)

    def told(inbox: list[dict[str, Any]]) -> int:
        return sum(n["title"] == "Failed: Flaky nightly" for n in inbox)

    try:
        await run_once()
        await run_once()  # still failing: no second notice
        assert told(await _inbox(engine, admin)) == 1
        outcome["fail"] = False
        await run_once()
        outcome["fail"] = True
        await run_once()  # failing again after it worked
        inbox = await _inbox(engine, admin)
    finally:
        jobs.JOBS.pop(full, None)
    assert told(inbox) == 2
    failed = next(n for n in inbox if n["title"] == "Failed: Flaky nightly")
    assert failed["severity"] == "error" and failed["kind"] == "sys.job_failed"
    assert str(failed["link"]).startswith("/form/sys.job_run/")


async def test_old_notifications_are_put_away(engine: AsyncEngine) -> None:
    anna = await _person(engine, "Anna")
    async with acting(engine, None) as records:
        await _send(records, title="Read", people=[anna])
        await _send(records, title="Unread", people=[anna])
        await records.conn.execute(
            text("UPDATE sys.notification SET read = true WHERE person = :p AND title = 'Read'"),
            {"p": anna},
        )
    async with acting(engine, None) as records:
        # 100 days on, the read one goes; a year on, the unread one too.
        later = await clean_up(records.conn, now=datetime.now(UTC) + timedelta(days=100))
    after_100_days = [n["title"] for n in await _inbox(engine, anna)]
    async with acting(engine, None) as records:
        await clean_up(records.conn, now=datetime.now(UTC) + timedelta(days=400))
    assert later >= 1 and after_100_days == ["Unread"]
    assert await _inbox(engine, anna) == []
