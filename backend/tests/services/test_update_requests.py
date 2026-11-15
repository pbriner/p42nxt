"""Updates requested in the app and carried out by the updater on the machine."""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.services.update_requests import service
from p42cloud.ui.actions import service as actions
from tests.security.helpers import acting, change


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


@pytest.fixture
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.owner_database_url)
    yield made
    await made.dispose()


async def _sql(owner: AsyncEngine, statement: str, **params: object) -> list[dict[str, object]]:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            found = await conn.execute(text(statement), params)
            return [dict(r) for r in found.mappings()] if found.returns_rows else []


@pytest.fixture
async def offered(owner: AsyncEngine) -> AsyncGenerator[str]:
    """The core's next release, as the update check records it; no other request open."""
    version = f"9.{uuid.uuid4().int % 1000}.0"
    await _sql(
        owner,
        "UPDATE sys.update_request SET state = 'cancelled' WHERE state IN ('requested', 'running')",
    )
    await _sql(
        owner,
        "INSERT INTO sys.module_update (module, installed, available, kind, fits)"
        " VALUES ('sys', '0.1.0', :v, 'normal', true)",
        v=version,
    )
    yield version
    await _sql(
        owner, "UPDATE sys.module_update SET deleted = now() WHERE available = :v", v=version
    )


def test_tonight_is_the_next_three_oclock_where_the_installation_is() -> None:
    evening = datetime(2026, 11, 15, 20, 0, tzinfo=UTC)  # 21:00 in Zurich
    assert service.tonight("Europe/Zurich", evening) == datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
    early = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)  # 02:00 in Zurich
    assert service.tonight("Europe/Zurich", early) == datetime(2026, 11, 15, 2, 0, tzinfo=UTC)


async def test_a_request_is_checked(engine: AsyncEngine, offered: str) -> None:
    async with acting(engine, None) as records:
        with pytest.raises(Problem) as refused:
            await records.create("sys.update_request", {"target": "sys", "version": "99.0.0"})
        assert refused.value.code == "not_offered"
        made = await records.create("sys.update_request", {"target": "sys", "version": offered})
        assert made.values["state"] == "requested"
        with pytest.raises(Problem) as refused:
            await records.create("sys.update_request", {"target": "sys", "version": offered})
        assert refused.value.code == "one_at_a_time"
        with pytest.raises(Problem) as refused:
            await change(records, "sys.update_request", made.sys_id, {"state": "succeeded"})
        assert refused.value.code == "set_by_updater"
        with pytest.raises(Problem) as refused:
            await records.delete("sys.update_request", made.sys_id, made.etag)
        assert refused.value.code == "kept"
        cancelled = await change(records, "sys.update_request", made.sys_id, {"state": "cancelled"})
    assert cancelled.values["state"] == "cancelled"


async def test_the_updater_carries_it_out_and_administrators_hear(
    engine: AsyncEngine, owner: AsyncEngine, offered: str
) -> None:
    async with acting(engine, None) as records:
        admin = await records.create("sys.person", {"name": f"Admin {offered}"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin.sys_id})
        made = await records.create("sys.update_request", {"target": "sys", "version": offered})
    with working_as(Context(channel="updater")):
        async with owner_transaction(owner) as conn:
            found = await service.due(conn)
            assert found is not None and found["sys_id"] == made.sys_id
            assert await service.start(conn, made.sys_id)
            assert not await service.start(conn, made.sys_id)  # once
            assert await service.due(conn) is None
            await service.finish(
                conn,
                made.sys_id,
                state="succeeded",
                message="Updated.",
                backup="/backups/p42-before-update-1.dump",
                log="Upgrading the database …\n",
            )
            with pytest.raises(Problem):
                await service.finish(
                    conn, made.sys_id, state="failed", message="", backup=None, log=""
                )
    done = (
        await _sql(
            owner,
            "SELECT state, from_version, backup, log FROM sys.update_request WHERE sys_id = :i",
            i=made.sys_id,
        )
    )[0]
    assert done["state"] == "succeeded" and done["from_version"]
    assert done["backup"] == "/backups/p42-before-update-1.dump"
    told = await _sql(
        owner,
        "SELECT title FROM sys.notification WHERE kind = 'sys.update_finished' AND person = :p",
        p=admin.sys_id,
    )
    assert [t["title"] for t in told] == [f"Updated: The core {offered}"]


async def test_update_now_and_tonight_are_actions_on_an_available_update(
    engine: AsyncEngine, owner: AsyncEngine, offered: str
) -> None:
    row = await _sql(owner, "SELECT sys_id FROM sys.module_update WHERE available = :v", v=offered)
    update = row[0]["sys_id"]
    assert isinstance(update, uuid.UUID)
    async with acting(engine, None) as records:
        admin = await records.create("sys.person", {"name": f"Updater admin {offered}"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin.sys_id})
    async with acting(engine, admin.sys_id) as records:  # their code given just now
        said = await actions.run(
            records, "sys.module_update", "sys.update_tonight", update, given={}
        )
    assert "Requested tonight at 03:00" in str(said)
    waiting = await _sql(
        owner,
        "SELECT target, version, not_before > now() AS later, requested_by"
        " FROM sys.update_request WHERE version = :v",
        v=offered,
    )
    assert waiting == [
        {"target": "sys", "version": offered, "later": True, "requested_by": admin.sys_id}
    ]
    # Everyone is told beforehand, until it starts.
    planned = await _sql(
        owner,
        "SELECT a.kind, a.subject, a.valid_until = r.not_before AS until_then"
        " FROM sys.announcement a JOIN sys.update_request r ON r.sys_id = a.about_update"
        " WHERE r.version = :v",
        v=offered,
    )
    assert len(planned) == 1 and planned[0]["kind"] == "information"
    assert str(planned[0]["subject"]).startswith("Update planned for ")
    assert planned[0]["until_then"]


async def _shown(owner: AsyncEngine, request: uuid.UUID) -> list[str]:
    found = await _sql(
        owner,
        "SELECT kind FROM sys.announcement WHERE about_update = :r AND valid_from <= now()"
        " AND (valid_until IS NULL OR valid_until > now()) ORDER BY kind",
        r=request,
    )
    return [str(f["kind"]) for f in found]


async def test_the_updater_says_it_is_updating_and_stops_saying_it(
    engine: AsyncEngine, owner: AsyncEngine, offered: str
) -> None:
    async with acting(engine, None) as records:
        made = await records.create("sys.update_request", {"target": "sys", "version": offered})
    assert await _shown(owner, made.sys_id) == []  # now: the update itself is the message
    with working_as(Context(channel="updater")):
        async with owner_transaction(owner) as conn:
            assert await service.start(conn, made.sys_id)
    assert await _shown(owner, made.sys_id) == ["warning"]
    with working_as(Context(channel="updater")):
        async with owner_transaction(owner) as conn:
            await service.finish(
                conn, made.sys_id, state="succeeded", message="", backup=None, log=""
            )
    assert await _shown(owner, made.sys_id) == []


async def test_a_cancelled_plan_takes_its_message_back(
    engine: AsyncEngine, owner: AsyncEngine, offered: str
) -> None:
    later = datetime.now(UTC) + timedelta(hours=3)
    async with acting(engine, None) as records:
        made = await records.create(
            "sys.update_request", {"target": "sys", "version": offered, "not_before": later}
        )
        assert await _shown(owner, made.sys_id) == []  # not committed yet: seen from outside
        await change(records, "sys.update_request", made.sys_id, {"state": "cancelled"})
        left = await records.conn.scalar(
            text(
                "SELECT count(*) FROM sys.announcement WHERE about_update = :r"
                " AND valid_until > now()"
            ),
            {"r": made.sys_id},
        )
    assert left == 0
    assert await _shown(owner, made.sys_id) == []
