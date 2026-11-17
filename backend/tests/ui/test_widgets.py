"""Totals for widgets, the widget catalog, and each person's dashboard."""

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

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


def _chart(**more: Any) -> Widget:
    return Widget("x", "X", "chart", list="sys.person", group="kind", chart="bar", **more)


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
        (_chart(dated="name"), "bad_field"),  # a time range over a date field
        (Widget("x", "X", "figure", list="sys.person", dated="created"), "bad_dated"),
        (_chart(param=("name", "sys.person")), "bad_param"),  # a reference to filter by
        (_chart(param=("created_by", "sys.group")), "bad_param"),  # to that table
        (
            Widget("x", "X", "list", list="sys.person", param=("created_by", "sys.person")),
            "bad_param",
        ),
    ],
)
async def test_widgets_are_checked(owner_engine: AsyncEngine, widget: Widget, code: str) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_widgets(conn, await load_catalog(conn), "sys", (widget,))
    assert code in [e.code for e in refused.value.errors]


class _Undo(Exception):
    pass


async def test_a_chart_may_take_a_choice_and_a_time_range(owner_engine: AsyncEngine) -> None:
    chart = _chart(param=("created_by", "sys.person"), dated="created")
    with working_as(Context(channel="upgrade")), pytest.raises(_Undo):
        async with owner_transaction(owner_engine) as conn:
            await write_widgets(conn, await load_catalog(conn), "sys", (chart,))
            row = (
                await conn.execute(
                    text("SELECT param, param_table, dated FROM sys.widget WHERE name = 'sys.x'")
                )
            ).one()
            assert tuple(row) == ("created_by", "sys.person", "created")
            raise _Undo  # nothing kept


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


async def _sql(owner: AsyncEngine, statement: str, **params: object) -> None:
    with working_as(Context(channel="upgrade")):
        async with owner_transaction(owner) as conn:
            await conn.execute(text(statement), params)


async def test_every_dashboard_offers_every_widget_its_own_placed(
    world: World, owner_engine: AsyncEngine
) -> None:
    name = f"sys.elsewhere_{world.tag}"
    await _sql(
        owner_engine,
        "INSERT INTO sys.widget (name, module, title, kind, dashboard, text, sizes, size, shown,"
        " position, custom, filled, measure) VALUES (:n, 'sys', '{\"en\": \"Elsewhere\"}',"
        " 'text', 'sys', '{\"en\": \"Hi\"}', '[\"s\"]', 's', 'default', 0, false, false,"
        " 'count')",
        n=name,
    )
    try:
        async with acting(world.engine, world.plain) as records:
            home = await dashboard(records, "home")
            own = await dashboard(records, "sys")
        offered = {w["name"]: w for w in home["widgets"]}
        assert offered[name]["shown"] == "available"  # offered on home, not placed there
        assert name not in [i["widget"] for i in home["items"]]
        assert own["widgets"][0]["name"] == name  # its own dashboard's first
        assert {"widget": name, "size": "s"} in own["items"]  # and placed there by default
    finally:
        await _sql(owner_engine, "UPDATE sys.widget SET deleted = now() WHERE name = :n", n=name)


async def test_a_placement_keeps_its_own_choice(world: World) -> None:
    plan = str(uuid.uuid4())
    async with acting(world.engine, world.plain) as records:
        found = await records.list(
            "sys.dashboard_layout", where={"field": "dashboard", "op": "eq", "value": "home"}
        )
        for old in found.records:
            await records.delete("sys.dashboard_layout", old.sys_id, old.etag)
        twice = [
            {"widget": "sys.people", "size": "s", "params": {"plan": plan}},
            {"widget": "sys.people", "size": "wide"},
        ]
        await records.create("sys.dashboard_layout", {"dashboard": "home", "items": twice})
        shown = await dashboard(records, "home")
        assert shown["items"] == twice  # the same widget twice, one with its own choice
        for bad in (
            {"plan": "not an id"},
            {"Bad Name": plan},
            {"a": plan, "b": plan, "c": plan, "d": plan},
        ):
            with pytest.raises(Problem):
                await records.create(
                    "sys.dashboard_layout",
                    {
                        "dashboard": "sys",
                        "items": [{"widget": "sys.people", "size": "s", "params": bad}],
                    },
                )
