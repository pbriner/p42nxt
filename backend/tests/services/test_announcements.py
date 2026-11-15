"""Messages for everyone: written by administrators, read by all, checked."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.services.announcements.service import notify_due
from p42cloud.services.notifications.service import recipients
from tests.security.helpers import acting, change


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def test_administrators_write_messages_everyone_reads_them(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        plain = (await records.create("sys.person", {"name": f"Reader {tag}"})).sys_id
        made = await records.create(
            "sys.announcement", {"kind": "warning", "subject": f"Maintenance {tag}"}
        )
    assert made.values["valid_from"] is not None  # shown from now when not said
    async with acting(engine, plain) as records:
        seen = await records.read("sys.announcement", made.sys_id)
        assert seen.values["subject"] == f"Maintenance {tag}"
        with pytest.raises(Problem) as refused:
            await records.create("sys.announcement", {"subject": "Mine"})
        assert refused.value.status == 403
        with pytest.raises(Problem):
            await change(records, "sys.announcement", made.sys_id, {"subject": "Changed"})


async def test_a_message_is_checked(engine: AsyncEngine) -> None:
    now = datetime.now(UTC)
    async with acting(engine, None) as records:
        with pytest.raises(Problem) as refused:
            await records.create(
                "sys.announcement",
                {
                    "subject": "Backwards",
                    "valid_from": now,
                    "valid_until": now - timedelta(hours=1),
                },
            )
        assert refused.value.code == "validation"
        made = await records.create(
            "sys.announcement", {"subject": "Fine", "valid_until": now + timedelta(hours=1)}
        )
        with pytest.raises(Problem) as refused:
            await change(
                records,
                "sys.announcement",
                made.sys_id,
                {"valid_until": (now - timedelta(days=1)).isoformat()},
            )
        assert refused.value.code == "validation"
        with pytest.raises(Problem) as refused:
            await records.create(
                "sys.announcement", {"subject": "Sneaky", "about_update": str(uuid.uuid4())}
            )
        assert refused.value.code in ("set_by_updater", "read_only", "validation")
        with pytest.raises(Problem) as refused:
            await change(records, "sys.announcement", made.sys_id, {"notified": now.isoformat()})
        assert refused.value.code in ("set_by_updater", "read_only", "validation")


async def test_a_message_for_a_group_is_only_for_its_members(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        group = await records.create("sys.group", {"name": f"Investors {tag}"})
        member = (await records.create("sys.person", {"name": f"Member {tag}"})).sys_id
        other = (await records.create("sys.person", {"name": f"Other {tag}"})).sys_id
        await records.create("sys.membership", {"group": group.sys_id, "person": member})
        made = await records.create(
            "sys.announcement", {"subject": f"For investors {tag}", "audience": group.sys_id}
        )
    async with acting(engine, member) as records:
        assert (await records.read("sys.announcement", made.sys_id)).values["subject"]
    async with acting(engine, other) as records:
        with pytest.raises(Problem) as hidden:
            await records.read("sys.announcement", made.sys_id)
        assert hidden.value.status in (403, 404)
        shown = await records.list(
            "sys.announcement", where={"field": "sys_id", "op": "eq", "value": str(made.sys_id)}
        )
        assert shown.records == []


async def _told(records: Records, person: uuid.UUID, subject: str) -> int:
    found = await records.conn.scalar(
        text(
            "SELECT count(*) FROM sys.notification WHERE person = :p AND title = :t"
            " AND kind = 'sys.message'"
        ),
        {"p": person, "t": subject},
    )
    return int(found or 0)


async def test_send_reaches_a_groups_members_or_everyone(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        group = await records.create("sys.group", {"name": f"Team {tag}"})
        member = (await records.create("sys.person", {"name": f"In {tag}"})).sys_id
        gone = (await records.create("sys.person", {"name": f"Left {tag}"})).sys_id
        await records.create("sys.membership", {"group": group.sys_id, "person": member})
        left = await records.create("sys.membership", {"group": group.sys_id, "person": gone})
        await records.delete("sys.membership", left.sys_id, left.etag)
        conn = records.conn
        assert await recipients(conn, (), (), (group.sys_id,)) == [member]
        everyone = await recipients(conn, (), (), everyone=True)
        services = await conn.scalar(
            text("SELECT count(*) FROM sys.person WHERE sys_id = ANY(:e) AND kind = 'service'"),
            {"e": everyone},
        )
    assert {member, gone} <= set(everyone)
    assert services == 0


async def test_a_message_goes_out_as_notifications_once(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    tag = uuid.uuid4().hex[:6]
    later = datetime.now(UTC) + timedelta(seconds=2)
    async with acting(engine, None) as records:
        group = await records.create("sys.group", {"name": f"Told {tag}"})
        member = (await records.create("sys.person", {"name": f"Told {tag}"})).sys_id
        await records.create("sys.membership", {"group": group.sys_id, "person": member})
        now_subject, later_subject, band_subject = (f"{w} {tag}" for w in ("Now", "Later", "Band"))
        for subject, deliver, start in (
            (now_subject, "both", None),
            (later_subject, "notification", later),
            (band_subject, "band", None),
        ):
            await records.create(
                "sys.announcement",
                {
                    "subject": subject,
                    "audience": group.sys_id,
                    "deliver": deliver,
                    **({"valid_from": start} if start else {}),
                },
            )
        assert await _told(records, member, now_subject) == 1  # right away
        assert await _told(records, member, later_subject) == 0  # not yet
        assert await _told(records, member, band_subject) == 0  # the band only
    owner = create_engine(upgraded.owner_database_url)
    try:
        async with owner.begin() as conn:
            await conn.execute(
                text("UPDATE sys.announcement SET valid_from = now() WHERE subject = :s"),
                {"s": later_subject},
            )
    finally:
        await owner.dispose()
    assert await notify_due(engine) >= 1
    await notify_due(engine)  # once only
    async with acting(engine, None) as records:
        assert await _told(records, member, later_subject) == 1
        assert await _told(records, member, now_subject) == 1
