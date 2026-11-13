"""The screens' records: navigation, list views, saved views, themes, preferences, favorites,
and the search across lists."""

import os
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.ui.navigation.models import Item
from p42cloud.ui.navigation.service import navigation, write_navigation
from p42cloud.ui.search.service import search
from p42cloud.web.app import create_app
from tests.security.helpers import acting, change

SITE = "https://p42.test"


@dataclass
class People:
    engine: AsyncEngine
    admin: uuid.UUID
    plain: uuid.UUID
    tag: str


@pytest.fixture(scope="module")
async def people(upgraded: Settings) -> AsyncGenerator[People]:
    engine = create_engine(upgraded.database_url)
    tag = uuid.uuid4().hex[:8]
    async with acting(engine, None) as records:
        admin = (await records.create("sys.person", {"name": f"Admin {tag}"})).sys_id
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin})
        plain = (
            await records.create(
                "sys.person", {"name": f"Plain {tag}", "email": f"plain{tag}@x.test"}
            )
        ).sys_id
    yield People(engine, admin, plain, tag)
    await engine.dispose()


def _items(found: dict[str, Any]) -> dict[str, list[str]]:
    """Section label: item names, over every module."""
    return {
        f"{m['id']}:{s['label']}": [i["name"] for i in s["items"]]
        for c in found["categories"]
        for m in c["modules"]
        for s in m["sections"]
    }


# ---- navigation ----


async def test_the_navigator_shows_only_what_one_may_open(people: People) -> None:
    async with acting(people.engine, people.admin) as records:
        admins = _items(await navigation(records))
    async with acting(people.engine, people.plain) as records:
        everyone = await navigation(records)
    assert "sys.modules" in admins["sys:Modules and data"]
    assert "sys.roles" in admins["sys:People and access"]
    plain = _items(everyone)
    assert "sys.people" in plain["sys:People and access"]  # everyone sees who's who
    assert "sys.roles" not in plain["sys:People and access"]
    assert everyone["categories"][-1]["name"] == "Platform"


@pytest.mark.parametrize(
    ("item", "code"),
    [
        (Item("x", "X", "sys.nowhere"), "unknown_table"),
        (Item("x", "X", kind="page", path="https://evil.example"), "bad_path"),
        (Item("x", "X"), "no_table"),
        (Item("x", "X", "sys.person", view="sys.nothing"), "unknown_view"),
        (Item("Bad", "X", "sys.person"), "bad_name"),
        (Item("x", "X", "sys.person", filter={"field": "colour", "op": "eq"}), "unknown_field"),
    ],
)
async def test_navigation_is_checked_before_it_is_written(
    owner_engine: AsyncEngine, item: Item, code: str
) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_navigation(conn, await load_catalog(conn), "sys", (item,))
    assert code in [e.code for e in refused.value.errors]


# ---- list views ----


async def test_every_core_table_has_a_list_view(people: People) -> None:
    async with acting(people.engine, people.admin) as records:
        views = await records.list("sys.list_view", limit=500)
    by_table = {r.values["table"]: r.values for r in views.records}
    core = {k for k, t in records.catalog.tables.items() if t.schema == "sys" and not t.retired}
    assert core - set(by_table) == set()
    assert by_table["sys.person"]["searchable"] is True
    assert by_table["sys.session"]["searchable"] is False  # made, not declared: not searched
    assert by_table["sys.session"]["columns"]


async def test_saved_views_are_ones_own_and_checked(people: People) -> None:
    async with acting(people.engine, people.plain) as records:
        first = await records.create(
            "sys.saved_view",
            {"view": "sys.person", "name": "  Mine ", "columns": ["name"], "is_default": True},
        )
        second = await records.create(
            "sys.saved_view",
            {
                "view": "sys.person",
                "name": "Active",
                "filter": {"field": "active", "op": "eq", "value": True},
                "is_default": True,
            },
        )
        again = await records.read("sys.saved_view", first.sys_id)
        refusals: list[str] = []
        for values in (
            {"view": "sys.person", "name": "X", "columns": ["colour"]},
            {"view": "sys.person", "name": "X", "filter": {"field": "colour", "op": "eq"}},
            {"view": "sys.nothing", "name": "X"},
            {"view": "sys.person", "name": "   "},
        ):
            with pytest.raises(Problem) as refused:
                await records.create("sys.saved_view", values)
            refusals.append(refused.value.code)
    async with acting(people.engine, people.admin) as records:
        admins_see = await records.list(
            "sys.saved_view", where={"field": "sys_id", "op": "eq", "value": str(first.sys_id)}
        )
    assert first.values["person"] == people.plain and first.values["name"] == "Mine"
    assert again.values["is_default"] is False and second.values["is_default"] is True
    assert refusals == ["validation"] * 4
    assert admins_see.records == []  # a personal table: not even administrators


async def test_a_list_is_remembered_once_per_person(people: People) -> None:
    async with acting(people.engine, people.plain) as records:
        await records.create("sys.view_state", {"view": "sys.group", "sort": ["-name"]})
        with pytest.raises(Problem):
            await records.create("sys.view_state", {"view": "sys.group", "sort": ["name"]})


# ---- themes and preferences ----


async def test_themes_admins_make_are_checked_and_module_themes_kept(people: People) -> None:
    async with acting(people.engine, people.admin) as records:
        violet = (
            await records.list("sys.theme", where={"field": "name", "op": "eq", "value": "violet"})
        ).records[0]
        with pytest.raises(Problem) as kept:
            await change(records, "sys.theme", violet.sys_id, {"radius": 12})
        with pytest.raises(Problem) as wrong:
            await records.create(
                "sys.theme",
                {"name": f"t{people.tag}", "label": "Mine", "colors": {"primary": ["red", "x"]}},
            )
        made = await records.create(
            "sys.theme",
            {
                "name": f"t{people.tag}",
                "label": "Mine",
                "colors": {"primary": ["#112233", "#445566"]},
                "radius": 8,
            },
        )
    assert kept.value.code == "declared_only"
    assert wrong.value.code == "validation"
    assert (made.values["custom"], made.values["module"]) == (True, "u")


async def test_preferences_are_checked(people: People) -> None:
    async with acting(people.engine, people.plain) as records:
        for values in ({"page_size": 33}, {"theme": "nonexistent"}):
            with pytest.raises(Problem) as refused:
                await records.create("sys.preference", values)
            assert refused.value.code == "validation"
        made = await records.create("sys.preference", {"mode": "dark", "theme": "blue"})
        with pytest.raises(Problem):
            await records.create("sys.preference", {"mode": "light"})  # one per person
    assert made.values["person"] == people.plain and made.values["page_size"] == 20


# ---- favorites ----


async def test_favorites_and_their_folders(people: People) -> None:
    async with acting(people.engine, people.plain) as records:
        folder = await records.create("sys.favorite", {"kind": "folder", "label": " Daily  "})
        inside = await records.create(
            "sys.favorite", {"item": "sys.people", "parent": str(folder.sys_id)}
        )
        refusals: list[str] = []
        for values in (
            {"kind": "folder", "label": " "},
            {"item": "sys.nowhere"},
            {"kind": "folder", "label": "Deeper", "parent": str(folder.sys_id)},
            {"item": "sys.people", "label": "Renamed"},
        ):
            with pytest.raises(Problem) as refused:
                await records.create("sys.favorite", values)
            refusals.append(refused.value.code)
        await records.delete("sys.favorite", folder.sys_id, folder.etag)
        moved = await records.read("sys.favorite", inside.sys_id)
    async with acting(people.engine, people.admin) as records:
        with pytest.raises(Problem):
            await records.create(
                "sys.favorite", {"item": "sys.people", "parent": str(folder.sys_id)}
            )  # not their folder
    assert folder.values["label"] == "Daily"
    assert refusals == ["validation"] * 4
    assert moved.values["parent"] is None  # the folder went, the favorite stayed


# ---- search ----


async def test_search_finds_only_what_one_may_read(people: People) -> None:
    async with acting(people.engine, None) as records:
        await records.create("sys.person", {"name": "Other", "email": f"other{people.tag}@x.test"})
    async with acting(people.engine, people.admin) as records:
        others_by_admin = await search(records, f"other{people.tag}@x")
        by_admin = await search(records, f"plain{people.tag}")
        by_name = await search(records, f"Plain {people.tag}", scope="sys.person")
    async with acting(people.engine, people.plain) as records:
        own = await search(records, f"plain{people.tag}@x")  # their own record, all of it
        others = await search(records, f"other{people.tag}@x")  # an email they can't read
        by_name_too = await search(records, "Other")  # but the name they can
        with pytest.raises(Problem) as short:
            await search(records, "p")
    (group,) = by_admin["groups"]
    assert group["list"] == "sys.person" and group["total"] == 1
    assert group["records"][0]["title"].startswith(f"Plain {people.tag}")
    assert by_name["groups"][0]["total"] == 1
    assert own["groups"][0]["total"] == 1
    assert by_name_too["groups"][0]["total"] >= 1
    assert others_by_admin["groups"][0]["total"] == 1 and others["groups"] == []
    assert short.value.status == 422


# ---- over HTTP ----


async def test_over_http(people: People, upgraded: Settings) -> None:
    app = create_app(
        upgraded.model_copy(update={"public_url": SITE}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SITE) as client:
            default = await client.get("/api/v1/theme")
            blue = await client.get("/api/v1/theme", params={"name": "blue"})
            unknown = await client.get("/api/v1/theme", params={"name": "nothing"})
            closed = await client.get("/api/v1/navigation")
            scopes = await client.get("/api/v1/search/scopes")
    assert default.json()["name"] == "violet"
    assert default.json()["colors"]["primary"] == {"light": "#7d00fa", "dark": "#d5baff"}
    assert (default.json()["cards"], blue.json()["name"]) == ("raised", "blue")
    assert unknown.json()["name"] == "violet"
    assert (closed.status_code, scopes.status_code) == (401, 401)
