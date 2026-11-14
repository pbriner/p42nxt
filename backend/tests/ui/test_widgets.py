"""Totals for widgets, the widget catalog, and each person's dashboard."""

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.aggregates import aggregate
from p42cloud.data.catalog import load_catalog
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS, EVERYONE
from p42cloud.ui.widgets.models import Widget
from p42cloud.ui.widgets.service import dashboard, write_widgets
from tests.security.helpers import acting


@dataclass
class World:
    engine: AsyncEngine
    plain: uuid.UUID
    tag: str


@pytest.fixture(scope="module")
async def world(upgraded: Settings) -> AsyncGenerator[World]:
    engine = create_engine(upgraded.database_url)
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        for n in range(3):
            await records.create(
                "sys.person", {"name": f"Agg {tag} {n}", "email": f"a{n}{tag}@x.test"}
            )
        await records.create("sys.person", {"name": f"Agg {tag} robot", "kind": "service"})
        plain = (await records.create("sys.person", {"name": f"Plain {tag}"})).sys_id
    yield World(engine, plain, tag)
    await engine.dispose()


async def test_totals_per_group_are_named(world: World) -> None:
    mine = {"field": "name", "op": "starts_with", "value": f"Agg {world.tag}"}
    async with acting(world.engine, None) as records:
        by_kind = await aggregate(records, "sys.person", where=mine, group="kind")
        by_month = await aggregate(
            records, "sys.person", where=mine, group="created", period="month"
        )
        top = await aggregate(records, "sys.person", where=mine, group="name", limit=1)
    assert by_kind.total == 4
    assert {(g["key"], g["label"], g["value"]) for g in by_kind.groups} == {
        ("employee", "Employee", 3),
        ("service", "Service account", 1),
    }
    assert len(by_month.groups) == 1 and by_month.groups[0]["value"] == 4
    assert top.other == 3  # the groups past the limit, added up


async def test_totals_count_only_what_one_may_read(world: World) -> None:
    mine = {"field": "name", "op": "starts_with", "value": f"Agg {world.tag}"}
    async with acting(world.engine, world.plain) as records:
        by_email = await aggregate(records, "sys.person", where=mine, group="email")
        with pytest.raises(Problem) as closed:
            await aggregate(records, "sys.grant")
    # They see who's who, but not others' email: it counts as empty for them.
    assert [(g["key"], g["value"]) for g in by_email.groups] == [(None, 4)]
    assert closed.value.status == 403


@pytest.mark.parametrize(
    ("widget", "code"),
    [
        (Widget("x", "X", "figure", list="sys.nothing"), "unknown_view"),
        (Widget("x", "X", "chart", list="sys.person"), "no_group"),
        (Widget("x", "X", "figure", list="sys.person", measure="sum"), "no_field"),
        (Widget("x", "X", "figure", list="sys.person", measure="sum", field="name"), "bad_field"),
        (Widget("x", "X", "figure", list="sys.person", size="huge"), "bad_size"),
        (Widget("x", "X", "text"), "no_text"),
        (Widget("x", "X", "text", text="Hi", dashboard="hello"), "bad_dashboard"),
    ],
)
async def test_widgets_are_checked(owner_engine: AsyncEngine, widget: Widget, code: str) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_widgets(conn, await load_catalog(conn), "sys", (widget,))
    assert code in [e.code for e in refused.value.errors]


async def test_a_dashboard_starts_with_the_defaults_and_keeps_mandatory_widgets(
    world: World, owner_engine: AsyncEngine
) -> None:
    async with acting(world.engine, world.plain) as records:
        first = await dashboard(records, "home")
    people = next(w for w in first["widgets"] if w["name"] == "sys.people")
    assert people["shown"] == "available" and first["items"] == []  # offered, not placed
    assert first["new"] == []  # nothing is new before they arrange their own
    # Administrators make it mandatory for everyone.
    async with acting(world.engine, None) as records:
        made = await records.create(
            "sys.widget_audience", {"widget": "sys.people", "group": EVERYONE, "shown": "mandatory"}
        )
    try:
        async with acting(world.engine, world.plain) as records:
            await records.create("sys.dashboard_layout", {"dashboard": "home", "items": []})
            now = await dashboard(records, "home")
        assert now["items"] == [{"widget": "sys.people", "size": "s"}]
    finally:
        with working_as(Context(channel="upgrade")):
            async with owner_transaction(owner_engine) as conn:
                await conn.execute(
                    text("UPDATE sys.widget_audience SET deleted = now() WHERE sys_id = :i"),
                    {"i": made.sys_id},
                )
    assert ADMINISTRATORS  # the group that may set audiences
