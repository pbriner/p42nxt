"""Admins' own tables, tables described for the caller, records with display values and access."""

import os
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.presenting import annotate, describe
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.tokens.service import make_token
from p42cloud.services.tables.service import retire_table, save_table, table_from
from p42cloud.web.app import create_app
from tests.security.helpers import acting, context_of, role_id


@dataclass
class World:
    engine: AsyncEngine
    owner: AsyncEngine
    admin: uuid.UUID
    reader: uuid.UUID  # holds the table's reader role
    editor: uuid.UUID  # holds the table's editor role
    table: str  # its name, in u


def _body(name: str, **fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "label": "Task",
        "label_plural": "Tasks",
        "display": "title",
        "fields": {
            "title": {
                "type": "string",
                "label": "Title",
                "options": {"max_length": 40},
                "required": True,
            },
            "state": {
                "type": "choice",
                "label": "State",
                "options": {
                    "values": [
                        {"value": "open", "label": {"en": "Open", "de": "Offen"}},
                        {"value": "done", "label": {"en": "Done", "de": "Erledigt"}},
                    ]
                },
            },
            "assignee": {
                "type": "reference",
                "label": "Assignee",
                "options": {"table": "sys.person"},
            },
        }
        | fields,
    }


@pytest.fixture(scope="module")
async def world(upgraded: Settings) -> AsyncGenerator[World]:
    engine = create_engine(upgraded.database_url)
    owner = create_engine(upgraded.owner_database_url)
    name = f"task{uuid.uuid4().hex[:6]}"
    async with acting(engine, None) as records:
        admin, reader, editor = [
            (await records.create("sys.person", {"name": f"{n} {name}"})).sys_id
            for n in ("Admin", "Reader", "Editor")
        ]
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin})
    with working_as(await context_of(engine, admin)):
        done = await save_table(owner, table_from(_body(name)))
    assert f"Create the table u.{name}" in done
    async with acting(engine, None) as records:
        for person, role in ((reader, "reader"), (editor, "editor")):
            group = (await records.create("sys.group", {"name": f"{role} {name}"})).sys_id
            await records.create(
                "sys.group_role",
                {"group": group, "role": await role_id(records, f"u.{name}.{role}")},
            )
            await records.create("sys.membership", {"group": group, "person": person})
    yield World(engine, owner, admin, reader, editor, name)
    await owner.dispose()
    await engine.dispose()


# ---- admins' tables ----


async def test_an_admins_table_works_like_any_other(world: World) -> None:
    key = f"u.{world.table}"
    async with acting(world.engine, world.editor) as records:
        made = await records.create(key, {"title": "Write docs", "state": "open"})
    async with acting(world.engine, world.reader) as records:
        found = await records.read(key, made.sys_id)
        with pytest.raises(Problem) as read_only:
            await records.update(key, made.sys_id, {"title": "Mine"}, found.etag)
    assert found.values["title"] == "Write docs"
    assert read_only.value.status == 403


async def test_admins_change_their_tables_by_the_engines_rules(world: World) -> None:
    widened = _body(world.table, notes={"type": "text", "label": "Notes"})
    with working_as(await context_of(world.engine, world.admin)):
        done = await save_table(world.owner, table_from(widened))
        narrowed = _body(world.table)
        narrowed["fields"]["title"]["options"] = {"max_length": 5}
        with pytest.raises(Problem) as refused:
            await save_table(world.owner, table_from(narrowed))
    assert f"Add u.{world.table}.notes" in done
    assert refused.value.code == "unsafe_change"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"name": "x", "fields": {"a": {"type": "nothing"}}}, "unknown_type"),
        ({"name": "x", "fields": {"a": {"type": "string", "colour": 1}}}, "bad_field"),
        ({"name": "Bad Name", "fields": {"a": {"type": "text"}}}, "bad_name"),
    ],
)
async def test_tables_are_checked(world: World, body: dict[str, Any], code: str) -> None:
    context = await context_of(world.engine, world.admin)
    with pytest.raises(Problem) as refused, working_as(context):
        await save_table(world.owner, table_from(body))
    assert code in [e.code for e in refused.value.errors]


async def test_only_administrators_build_tables(world: World) -> None:
    context = await context_of(world.engine, world.editor)
    with pytest.raises(Problem) as refused, working_as(context):
        await save_table(world.owner, table_from(_body(f"x{uuid.uuid4().hex[:6]}")))
    assert refused.value.code == "admins_only"


async def test_a_retired_table_keeps_its_data_and_loses_its_roles(world: World) -> None:
    name = f"old{uuid.uuid4().hex[:6]}"
    with working_as(await context_of(world.engine, world.admin)):
        await save_table(world.owner, table_from(_body(name)))
        await retire_table(world.owner, name)
    async with acting(world.engine, None) as records:
        roles = await records.list(
            "sys.role", where={"field": "name", "op": "starts_with", "value": f"u.{name}."}
        )
        kept = await records.list(f"u.{name}")  # the system still reads it
    assert roles.records == []
    assert kept.records == []


# ---- described for the caller ----


async def test_a_table_is_described_for_each_person(world: World) -> None:
    async with acting(world.engine, world.reader) as records:
        table = (await load_catalog(records.conn)).tables[f"u.{world.table}"]
        for_reader = describe(table, records.access, "de")
    async with acting(world.engine, world.editor) as records:
        for_editor = describe(table, records.access, "en")
    state = next(f for f in for_reader["fields"] if f["name"] == "state")
    assert for_reader["label"] == "Task" and for_reader["display"] == "title"
    assert for_reader["operations"] == {
        "read": True,
        "create": False,
        "write": False,
        "delete": False,
        "restore": False,
    }
    assert for_editor["operations"]["write"] is True
    assert state["options"]["values"] == [
        {"value": "open", "label": "Offen", "tone": None},
        {"value": "done", "label": "Erledigt", "tone": None},
    ]
    assert (state["read"], state["write"], state["kind"]) == (True, False, "choice")
    assert any(f["name"] == "sys_id" and f["system"] for f in for_reader["fields"])


async def test_records_name_their_references_and_people_and_say_what_may_be_done(
    world: World,
) -> None:
    key = f"u.{world.table}"
    async with acting(world.engine, world.editor) as records:
        made = await records.create(
            key, {"title": "Review", "state": "done", "assignee": world.reader}
        )
    for person, may in ((world.reader, False), (world.editor, True)):
        async with acting(world.engine, person) as records:
            (body,) = await annotate(
                records, records.catalog.tables[key], [await records.read(key, made.sys_id)]
            )
        # Who made and changed it is named too: the form says "Created … by <name>".
        assert body["_display"] == {
            "state": "Done",
            "assignee": f"Reader {world.table}",
            "created_by": f"Editor {world.table}",
            "updated_by": f"Editor {world.table}",
        }
        assert body["_access"] == {"write": may, "delete": may, "restore": may}


# ---- over HTTP ----


async def test_over_http_tables_need_a_second_factor_and_lists_can_be_trimmed(
    world: World, upgraded: Settings
) -> None:
    async with acting(world.engine, None) as records:
        token = (await make_token(records.conn, world.admin, "Tables")).token
        reading = (await make_token(records.conn, world.reader, "Reading")).token
    app = create_app(
        upgraded.model_copy(update={"public_url": "https://p42.test"}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://p42.test") as client:
            built = await client.post("/api/v1/tables", json=_body("viahttp"), headers=headers)
            # An administrator isn't a reader of every table: the reader describes it.
            described = await client.get(
                f"/api/v1/meta/u/{world.table}", headers={"Authorization": f"Bearer {reading}"}
            )
            trimmed = await client.get(
                "/api/v1/sys/person", params={"fields": "name", "limit": 1}, headers=headers
            )
    assert built.json()["code"] == "verify_again"  # a token gives no second factor
    assert described.json()["table"] == f"u.{world.table}"
    (record,) = trimmed.json()["records"]
    assert set(record) == {"sys_id", "name", "_etag", "_display", "_access", "_actions"}
