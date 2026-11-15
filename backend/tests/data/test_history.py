"""Related lists from links to any record, notices about a record, and a record's history."""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.history import history
from p42cloud.data.presenting import annotate, describe
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.services.notifications.service import link_about, send
from tests.security.helpers import acting, change


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def test_links_to_any_record_give_related_lists(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        table = records.catalog.tables["sys.person"]
        described = describe(table, records.access, "en", records.catalog)
    by = {f"{r['table']}.{r['field']}": r for r in described["referenced_by"]}
    # Calls name their table by its name; shares by its record of sys.table.
    assert by["sys.outside_call.about"]["also"] == {"record_table": "sys.person"}
    assert by["sys.share.record"]["also"] == {"table": str(table.id)}
    assert by["sys.notification.about"]["also"] == {"about_table": "sys.person"}


async def test_a_notice_links_the_record_its_link_leads_to(
    engine: AsyncEngine, upgraded: Settings
) -> None:
    async with acting(engine, None) as records:
        person = await records.create("sys.person", {"name": f"Told {uuid.uuid4().hex[:6]}"})
        await send(
            records.conn,
            "sys.job_failed",
            "Failed: something",
            people=(person.sys_id,),
            link=f"/form/sys.person/{person.sys_id}",
        )
        await send(records.conn, "sys.job_failed", "Elsewhere", people=(person.sys_id,), link="/")
        rows = (
            await records.conn.execute(
                text(
                    "SELECT title, about_table, about FROM sys.notification WHERE person = :p"
                    " ORDER BY title"
                ),
                {"p": person.sys_id},
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("Elsewhere", None, None),
        ("Failed: something", "sys.person", person.sys_id),
    ]
    # From before: the upgrade links them.
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.execute(
                text(
                    "UPDATE sys.notification SET about = NULL, about_table = NULL WHERE person = :p"
                ),
                {"p": person.sys_id},
            )
            await link_about(conn)
            again = await conn.scalar(
                text("SELECT about FROM sys.notification WHERE person = :p AND link <> '/'"),
                {"p": person.sys_id},
            )
    await owner.dispose()
    assert again == person.sys_id


async def test_a_records_history_is_for_those_who_may_read_it(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        group = await records.create("sys.group", {"name": f"History {tag}"})
        await change(records, "sys.group", group.sys_id, {"description": "Changed."})
        plain = (await records.create("sys.person", {"name": f"Plain {tag}"})).sys_id
        entries = await history(records, "sys.group", group.sys_id)
    assert [e["operation"] for e in entries] == ["update", "insert"]  # newest first
    assert entries[0]["changes"] == {"description": [None, "Changed."]}
    assert entries[0]["by_name"] == "System"
    # A record they may not read (a grant: only administrators read those): no history either.
    async with acting(engine, None) as records:
        grant = await records.conn.scalar(text('SELECT sys_id FROM sys."grant" LIMIT 1'))
    assert isinstance(grant, uuid.UUID)
    async with acting(engine, plain) as records:
        with pytest.raises(Problem) as hidden:
            await history(records, "sys.grant", grant)
    assert hidden.value.status in (403, 404)


async def test_references_always_read_as_names(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        group = await records.create("sys.group", {"name": f"Retired {tag}"})
        plain = await records.create("sys.person", {"name": f"Plain {tag}"})
        member = await records.create(
            "sys.membership", {"group": group.sys_id, "person": plain.sys_id}
        )
        await records.delete("sys.membership", member.sys_id, member.etag)
        await records.delete(
            "sys.group", group.sys_id, (await records.read("sys.group", group.sys_id)).etag
        )
        # A retired target keeps its name, marked.
        table = records.catalog.tables["sys.membership"]
        rows = await records.list(
            "sys.membership",
            where={"field": "sys_id", "op": "eq", "value": str(member.sys_id)},
            deleted=True,
        )
        (body,) = await annotate(records, table, rows.records)
        grant = await records.conn.scalar(text('SELECT sys_id FROM sys."grant" LIMIT 1'))
        await send(
            records.conn,
            "sys.job_failed",
            "About a grant",
            people=(plain.sys_id,),
            link=f"/form/sys.grant/{grant}",
        )
        await send(
            records.conn,
            "sys.job_failed",
            "About a person",
            people=(plain.sys_id,),
            link=f"/form/sys.person/{plain.sys_id}",
        )
    assert body["_display"]["group"] == f"Retired {tag}"
    assert body["_reference_state"]["group"] == "retired"
    # As the person told: a link to any record is named too; one they can't open is hidden.
    async with acting(engine, plain.sys_id) as records:
        table = records.catalog.tables["sys.notification"]
        mine = await records.list(
            "sys.notification", where={"field": "person", "op": "eq", "value": str(plain.sys_id)}
        )
        bodies = {b["title"]: b for b in await annotate(records, table, mine.records)}
    assert bodies["About a person"]["_display"]["about"] == f"Plain {tag}"
    assert bodies["About a grant"]["_reference_state"]["about"] == "hidden"
    assert "about" not in bodies["About a grant"]["_display"]
