"""Comments and work notes on records, over the API: the record's rights (reading for comments,
changing for work notes), the author's own removal, plain text, and an event per entry."""

import os
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.metadata import version
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.secrets import Sealer
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.sdk import Client
from p42cloud.security.access.roles import Grant, Role
from p42cloud.security.tokens.service import make_token
from p42cloud.web.app import create_app
from tests.security.helpers import acting

SITE = "https://p42.test"
ALL = ("read", "create", "write", "delete", "restore")
MINE = {"field": "created_by", "op": "eq", "value": "@me"}


def _module(module_id: str) -> Module:
    receipt = Table(
        "receipt",
        "Receipt",
        {"text": String("Text", max_length=60)},
        comments=True,
        work_notes=True,
    )
    plain = Table("plain", "Plain", {"text": String("Text", max_length=60)})
    noted = Table("noted", "Noted", {"text": String("Text", max_length=60)}, work_notes=True)
    return Module(
        id=module_id,
        publisher="test",
        name="Comments",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(receipt, plain, noted),
        roles=(
            Role(
                f"{module_id}.keeper",
                "Keeper",
                (
                    Grant(f"{module_id}.receipt", ALL, condition=MINE),
                    Grant(f"{module_id}.plain", ALL),
                    Grant(f"{module_id}.noted", ALL),
                ),
            ),
            Role(f"{module_id}.reader", "Reader", (Grant(f"{module_id}.receipt", ("read",)),)),
        ),
    )


@pytest.fixture(scope="module")
async def app_engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def setup(
    upgraded: Settings,
    owner_engine: AsyncEngine,
    app_engine: AsyncEngine,
    new_module: Callable[[], Awaitable[str]],
) -> AsyncGenerator[dict[str, Any]]:
    """Ann and Ben keep receipts (each their own), Rea reads them all; one API client."""
    module_id = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(module_id)])
    people: dict[str, Any] = {"module": module_id}
    async with acting(app_engine, None) as records:
        for name, role in (("ann", "keeper"), ("ben", "keeper"), ("rea", "reader")):
            role_id = await records.conn.scalar(
                text("SELECT sys_id FROM sys.role WHERE name = :n"), {"n": f"{module_id}.{role}"}
            )
            group = await records.create("sys.group", {"name": f"{name} {uuid.uuid4().hex[:6]}"})
            await records.create("sys.group_role", {"group": group.sys_id, "role": role_id})
            person = await records.create("sys.person", {"name": f"{name} {module_id}"})
            await records.create("sys.membership", {"group": group.sys_id, "person": person.sys_id})
            made = await make_token(records.conn, person.sys_id, "Tests")
            people[name] = {"Authorization": f"Bearer {made.token}"}
    app = create_app(
        upgraded.model_copy(update={"public_url": SITE}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SITE) as client:
            people["api"] = client
            yield people


async def _receipt(setup: dict[str, Any], who: str, table: str = "receipt") -> str:
    api: httpx.AsyncClient = setup["api"]
    made = await api.post(
        f"/api/v1/{setup['module']}/{table}", json={"text": "Oil"}, headers=setup[who]
    )
    assert made.status_code == 201, made.text
    return str(made.json()["sys_id"])


async def test_readers_comment_and_authors_remove_their_own(
    setup: dict[str, Any], app_engine: AsyncEngine
) -> None:
    api: httpx.AsyncClient = setup["api"]
    receipt = await _receipt(setup, "ann")
    comments = f"/api/v1/{setup['module']}/receipt/{receipt}/comments"
    first = await api.post(comments, json={"text": "  Paid in March.  "}, headers=setup["ann"])
    assert first.status_code == 201, first.text
    second = await api.post(comments, json={"text": "<b>Checked</b>"}, headers=setup["rea"])
    assert second.status_code == 201  # Rea only reads the receipt, and may comment
    listed = (await api.get(comments, headers=setup["ann"])).json()["comments"]
    assert [c["text"] for c in listed] == ["<b>Checked</b>", "Paid in March."]  # kept as text
    assert [c["mine"] for c in listed] == [False, True]
    assert listed[1]["by_name"].startswith("ann")

    gone = await api.delete(f"/api/v1/comments/{listed[0]['sys_id']}", headers=setup["ann"])
    assert gone.status_code == 403  # not Ann's
    gone = await api.delete(f"/api/v1/comments/{listed[0]['sys_id']}", headers=setup["rea"])
    assert gone.status_code == 204
    after = (await api.get(comments, headers=setup["ann"])).json()["comments"]
    assert [c["text"] for c in after] == ["Paid in March."]

    # Ben may not read Ann's receipt: neither its comments nor commenting.
    assert (await api.get(comments, headers=setup["ben"])).status_code == 404
    refused = await api.post(comments, json={"text": "Hi"}, headers=setup["ben"])
    assert refused.status_code == 404
    other = await api.delete(f"/api/v1/comments/{after[0]['sys_id']}", headers=setup["ben"])
    assert other.status_code == 404

    async with acting(app_engine, None) as records:
        published = await records.conn.scalar(
            text(
                "SELECT count(*) FROM sys.event WHERE type = 'sys.comment_added'"
                " AND about = CAST(:r AS uuid)"
            ),
            {"r": receipt},
        )
    assert published == 2


async def test_only_words_on_records_with_a_conversation(setup: dict[str, Any]) -> None:
    api: httpx.AsyncClient = setup["api"]
    receipt = await _receipt(setup, "ann")
    comments = f"/api/v1/{setup['module']}/receipt/{receipt}/comments"
    empty = await api.post(comments, json={"text": "   "}, headers=setup["ann"])
    assert empty.status_code == 422
    long = await api.post(comments, json={"text": "x" * 5001}, headers=setup["ann"])
    assert long.status_code == 422
    plain = await _receipt(setup, "ann", "plain")
    none = await api.post(
        f"/api/v1/{setup['module']}/plain/{plain}/comments",
        json={"text": "Hi"},
        headers=setup["ann"],
    )
    assert none.status_code == 404


async def test_work_notes_are_for_those_who_may_change_the_record(
    setup: dict[str, Any], app_engine: AsyncEngine
) -> None:
    api: httpx.AsyncClient = setup["api"]
    receipt = await _receipt(setup, "ann")
    said = f"/api/v1/{setup['module']}/receipt/{receipt}/comments"
    note = await api.post(
        said, json={"text": "Ask the roofer again.", "kind": "work_note"}, headers=setup["ann"]
    )
    assert note.status_code == 201, note.text
    assert note.json()["kind"] == "work_note"
    await api.post(said, json={"text": "Paid."}, headers=setup["ann"])
    # Ann changes the receipt: she reads both. Rea only reads it: comments only.
    mine = (await api.get(said, headers=setup["ann"])).json()["comments"]
    assert [(c["kind"], c["text"]) for c in mine] == [
        ("comment", "Paid."),
        ("work_note", "Ask the roofer again."),
    ]
    theirs = (await api.get(said, headers=setup["rea"])).json()["comments"]
    assert [c["text"] for c in theirs] == ["Paid."]
    refused = await api.post(
        said, json={"text": "Me too", "kind": "work_note"}, headers=setup["rea"]
    )
    assert refused.status_code == 403
    hidden = await api.delete(f"/api/v1/comments/{note.json()['sys_id']}", headers=setup["rea"])
    assert hidden.status_code == 404  # Rea doesn't learn there is one
    wrong = await api.post(said, json={"text": "Hi", "kind": "memo"}, headers=setup["ann"])
    assert wrong.status_code == 422

    async with acting(app_engine, None) as records:
        published = (
            await records.conn.execute(
                text(
                    "SELECT type, data FROM sys.event WHERE about = CAST(:r AS uuid)"
                    " ORDER BY created"
                ),
                {"r": receipt},
            )
        ).all()
    assert [(p.type, list(p.data)) for p in published] == [
        ("sys.work_note_added", ["work_note"]),
        ("sys.comment_added", ["comment"]),
    ]


async def test_a_table_with_work_notes_only(setup: dict[str, Any]) -> None:
    api: httpx.AsyncClient = setup["api"]
    noted = await _receipt(setup, "ann", "noted")
    said = f"/api/v1/{setup['module']}/noted/{noted}/comments"
    assert (await api.post(said, json={"text": "Hi"}, headers=setup["ann"])).status_code == 404
    made = await api.post(said, json={"text": "Hi", "kind": "work_note"}, headers=setup["ann"])
    assert made.status_code == 201
    table = (await api.get(f"/api/v1/meta/{setup['module']}/noted", headers=setup["ann"])).json()
    assert (table["comments"], table["work_notes"]) == (False, True)


async def test_modules_comment_and_note_through_the_sdk(
    setup: dict[str, Any], app_engine: AsyncEngine
) -> None:
    receipt = await _receipt(setup, "ann")
    table = f"{setup['module']}.receipt"
    async with acting(app_engine, None) as records:
        client = Client(records)
        await client.comment(table, receipt, "Closed by the rule.", kind="work_note")
        await client.comment(table, receipt, "Done.")
        found = await client.comments(table, receipt)
    assert [(c["kind"], c["text"]) for c in found] == [
        ("comment", "Done."),
        ("work_note", "Closed by the rule."),
    ]
