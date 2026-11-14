"""Module routes: declared, checked, mounted before the record routes, for signed-in callers."""

import dataclasses
import os
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import httpx
import pytest

from p42cloud.data.catalog import load_catalog
from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.modules.core import CORE
from p42cloud.sdk.client import Client
from p42cloud.security.tokens.service import make_token
from p42cloud.services.routes.models import Route, RouteRequest
from p42cloud.services.routes.service import register, route_problems
from p42cloud.ui.widgets.models import Widget
from p42cloud.ui.widgets.service import _problems  # pyright: ignore[reportPrivateUsage]
from p42cloud.web import app as web_app
from p42cloud.web.app import create_app
from p42cloud.web.module_routes import _endpoint  # pyright: ignore[reportPrivateUsage]
from p42cloud.web.permissions import declared
from tests.security.helpers import acting

SITE = "https://p42.example.com"


async def _hello(request: RouteRequest) -> dict[str, Any]:
    try:  # the caller's grants apply: only administrators read jobs
        await Client.of(request).list("sys.job", limit=1)
        sees_jobs = True
    except Problem:
        sees_jobs = False
    return {
        "word": request.params["word"],
        "loud": request.query.get("loud") == "1",
        "sees_jobs": sees_jobs,
    }


async def _echo(request: RouteRequest) -> Any:
    return {"got": request.body}


@pytest.fixture(scope="module")
def routes() -> Iterator[None]:
    """The core with two routes of its own, as a module would declare them."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            web_app,
            "CORE",
            dataclasses.replace(
                CORE,
                routes=(
                    Route("GET", "/route_test/{word}", _hello, "Says the word back."),
                    Route("POST", "/route_test", _echo),
                ),
            ),
        )
        yield
    register("sys", ())


@pytest.fixture(scope="module")
async def api(upgraded: Settings, routes: None) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(
        upgraded.model_copy(update={"public_url": SITE}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SITE) as client:
            yield client


@pytest.fixture(scope="module")
async def token(upgraded: Settings) -> str:
    engine = create_engine(upgraded.database_url)
    try:
        async with acting(engine, None) as records:
            person = await records.create("sys.person", {"name": f"Caller {uuid.uuid4().hex[:6]}"})
            made = await make_token(records.conn, person.sys_id, "Routes")
    finally:
        await engine.dispose()
    return made.token


async def test_a_module_route_answers_signed_in_callers_only(
    api: httpx.AsyncClient, token: str
) -> None:
    auth = {"Authorization": f"Bearer {token}"}
    anonymous = await api.get("/api/v1/sys/route_test/hi")
    said = await api.get("/api/v1/sys/route_test/hi", params={"loud": "1"}, headers=auth)
    echoed = await api.post("/api/v1/sys/route_test", json={"a": [1, 2]}, headers=auth)
    broken = await api.post(
        "/api/v1/sys/route_test",
        content=b"{not json",
        headers=auth | {"Content-Type": "application/json"},
    )
    records = await api.get("/api/v1/sys/person", headers=auth)  # the generic routes still work
    assert anonymous.status_code == 401
    assert said.json() == {"word": "hi", "loud": True, "sees_jobs": False}
    assert echoed.json() == {"got": {"a": [1, 2]}}
    assert broken.status_code == 400
    assert records.status_code == 200


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/person/stats", "taken"),  # the core's table: would hide /sys/person/<id>
        ("/action/x", "taken"),
        ("/{plan}", "taken"),
        ("widget/x", "bad_path"),
        ("/Widget", "bad_path"),
        ("/a/../b", "bad_path"),
    ],
)
def test_routes_cant_hide_the_generic_ones(path: str, code: str) -> None:
    found = route_problems((Route("GET", path, _echo),), {"person", "group"})
    assert [e.code for e in found] == [code]


def test_a_module_route_is_for_signed_in_callers() -> None:
    endpoint = _endpoint("sys", Route("GET", "/x", _echo))  # pyright: ignore[reportPrivateUsage]
    assert declared(endpoint) == "signed_in"


def test_routes_are_declared_once() -> None:
    twice = (Route("GET", "/a", _echo), Route("GET", "/a", _echo))
    assert [e.code for e in route_problems(twice, set())] == ["duplicate"]


async def test_a_source_widget_names_a_get_route_of_its_module(upgraded: Settings) -> None:
    engine = create_engine(upgraded.database_url)
    try:
        async with acting(engine, None) as records:
            catalog = await load_catalog(records.conn)
    finally:
        await engine.dispose()
    views = {"sys.person": "sys.person"}
    good = Widget("x", "X", "figure", list="sys.person", source="/widget/x")
    unknown = Widget("x", "X", "figure", list="sys.person", source="/widget/y")
    text = Widget("x", "X", "text", text="Hi", source="/widget/x")
    assert _problems(catalog, views, "sys", good, {"/widget/x"}) == []
    assert [e.code for e in _problems(catalog, views, "sys", unknown, {"/widget/x"})] == [
        "unknown_source"
    ]
    assert "bad_source" in [e.code for e in _problems(catalog, views, "sys", text, {"/widget/x"})]
