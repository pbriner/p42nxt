"""The HTTP API: who may call what, records over HTTP, signing in with cookies, problems."""

import base64
import json
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import text

from p42cloud.data import installation
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.sign_in import credentials, totp
from p42cloud.security.tokens.service import make_token
from p42cloud.web.app import create_app
from p42cloud.web.permissions import declared
from tests.security.helpers import acting

SITE = "https://p42.test"
PASSWORD = "correct horse battery staple"
OPENAPI = Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi.json"
PROBLEM = "application/problem+json"


def _app(upgraded: Settings, sealer: Sealer, **settings: Any) -> FastAPI:
    changed = upgraded.model_copy(update={"public_url": SITE} | settings)
    return create_app(changed, sealer=sealer, upgrade=False)


@pytest.fixture(scope="module")
def sealer() -> Sealer:
    return Sealer(os.urandom(32))


@pytest.fixture(scope="module")
async def api(upgraded: Settings, sealer: Sealer) -> AsyncGenerator[httpx.AsyncClient]:
    app = _app(upgraded, sealer)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SITE) as client:
            yield client


@pytest.fixture(scope="module")
async def admin(upgraded: Settings) -> tuple[str, str]:
    """An administrator with an API token; returns (person, token)."""
    engine = create_engine(upgraded.database_url)
    try:
        async with acting(engine, None) as records:
            person = await records.create("sys.person", {"name": f"Admin {uuid.uuid4().hex[:6]}"})
            await records.create(
                "sys.membership", {"group": ADMINISTRATORS, "person": person.sys_id}
            )
            made = await make_token(records.conn, person.sys_id, "Tests")
    finally:
        await engine.dispose()
    return str(person.sys_id), made.token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- who may call what ----


def test_every_route_declares_who_may_call_it(upgraded: Settings, sealer: Sealer) -> None:
    app = _app(upgraded, sealer)
    missing = [
        f"{sorted(r.methods or ())} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute) and declared(r.endpoint) is None
    ]
    assert missing == []


async def test_answers_carry_protective_headers(api: httpx.AsyncClient) -> None:
    answer = await api.get("/api/v1/health")
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert answer.headers["x-frame-options"] == "DENY"
    assert answer.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in answer.headers["content-security-policy"]


async def test_health_is_public_and_records_are_not(api: httpx.AsyncClient) -> None:
    health = await api.get("/api/v1/health")
    records = await api.get("/api/v1/sys/person")
    assert health.json()["status"] == "ok"
    assert records.status_code == 401
    assert records.headers["content-type"] == PROBLEM
    assert records.json()["code"] == "not_signed_in"


# ---- records over HTTP ----


async def test_records_over_http_with_a_token(
    api: httpx.AsyncClient, admin: tuple[str, str]
) -> None:
    _, token = admin
    tag = uuid.uuid4().hex[:6]
    made = await api.post(
        "/api/v1/sys/person", json={"name": f"Http {tag}"}, headers=_bearer(token)
    )
    assert made.status_code == 201
    location, etag = made.headers["location"], made.headers["etag"]
    assert location == f"/api/v1/sys/person/{made.json()['sys_id']}"
    assert made.json()["source"] == "api:token:Tests"

    missing = await api.patch(location, json={"name": "Changed"}, headers=_bearer(token))
    stale = await api.patch(
        location, json={"name": "Changed"}, headers=_bearer(token) | {"If-Match": '"old"'}
    )
    changed = await api.patch(
        location, json={"name": f"Changed {tag}"}, headers=_bearer(token) | {"If-Match": etag}
    )
    assert (missing.status_code, stale.status_code, changed.status_code) == (428, 412, 200)
    assert stale.json()["current"]["name"] == f"Http {tag}"

    simple = await api.get(
        "/api/v1/sys/person", params={"filter[name][contains]": tag}, headers=_bearer(token)
    )
    where = await api.get(
        "/api/v1/sys/person",
        params={
            "where": json.dumps({"field": "name", "op": "starts_with", "value": "changed"}),
            "filter[name][ends_with]": tag,
        },
        headers=_bearer(token),
    )
    assert [r["name"] for r in simple.json()["records"]] == [f"Changed {tag}"]
    assert [r["name"] for r in where.json()["records"]] == [f"Changed {tag}"]

    gone = await api.delete(
        location, headers=_bearer(token) | {"If-Match": changed.headers["etag"]}
    )
    back = await api.post(f"{location}/restore", headers=_bearer(token))
    assert gone.json()["deleted"] is not None
    assert back.json()["deleted"] is None


@pytest.mark.parametrize(
    "case",
    [
        ("/api/v1/sys/person/01900000-0000-7000-8000-00000000ffff", {}, 404, "not_found"),
        ("/api/v1/nowhere/thing", {}, 404, "no_table"),
        ("/api/v1/sys/person", {"limit": "0"}, 422, "validation"),
        ("/api/v1/sys/person", {"where": "not json"}, 400, "bad_where"),
        ("/api/v1/sys/person", {"filter[colour]": "red"}, 422, "invalid_condition"),
    ],
)
async def test_problems_say_what_went_wrong(
    api: httpx.AsyncClient, admin: tuple[str, str], case: tuple[str, dict[str, str], int, str]
) -> None:
    path, params, status, code = case
    answer = await api.get(path, params=params, headers=_bearer(admin[1]))
    body = answer.json()
    assert answer.status_code == status
    assert answer.headers["content-type"] == PROBLEM
    assert (body["code"], body["status"], body["type"]) == (
        code,
        status,
        f"/api/v1/problems/{code}",
    )


# ---- signing in with cookies ----


async def _person_with_app(upgraded: Settings, sealer: Sealer) -> tuple[str, bytes]:
    email = f"web{uuid.uuid4().hex[:6]}@example.com"
    engine = create_engine(upgraded.database_url)
    try:
        async with acting(engine, None) as records:
            person = await records.create("sys.person", {"name": "Web", "email": email})
            await credentials.set_password(records.conn, person.sys_id, PASSWORD)
            made = await credentials.start_totp(records.conn, sealer, person.sys_id, "Phone")
            key = base64.b32decode(made.key + "=" * (-len(made.key) % 8))
            await credentials.confirm_totp(
                records.conn,
                sealer,
                person.sys_id,
                made.credential,
                totp.code(key, totp.step_of(datetime.now(UTC)) - 1),
            )
    finally:
        await engine.dispose()
    return email, key


async def test_signing_in_with_cookies_and_csrf(
    api: httpx.AsyncClient, upgraded: Settings, sealer: Sealer
) -> None:
    email, key = await _person_with_app(upgraded, sealer)
    api.cookies.clear()
    first = await api.post("/api/v1/sign-in", json={"email": email, "password": PASSWORD})
    assert first.json() == {"stage": "second_factor"}
    assert "httponly" in first.headers["set-cookie"].lower()
    code = totp.code(key, totp.step_of(datetime.now(UTC)))
    second = await api.post("/api/v1/sign-in/code", json={"code": code})
    assert second.json() == {"stage": "active"}
    csrf = api.cookies["p42_csrf"]

    me = await api.get("/api/v1/me")
    assert me.json()["person"]["email"] == email

    without = await api.post("/api/v1/undo", json={"request": str(uuid.uuid4())})
    foreign = await api.post(
        "/api/v1/undo",
        json={"request": str(uuid.uuid4())},
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    allowed = await api.post(
        "/api/v1/undo", json={"request": str(uuid.uuid4())}, headers={"X-CSRF-Token": csrf}
    )
    assert (without.status_code, without.json()["code"]) == (403, "csrf")
    assert (foreign.status_code, foreign.json()["code"]) == (403, "wrong_origin")
    assert allowed.status_code == 200

    out = await api.post("/api/v1/sign-in/out", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 200
    assert (await api.get("/api/v1/me")).status_code == 401


async def _limit(upgraded: Settings, per_minute: int) -> None:
    owner = create_engine(upgraded.owner_database_url)
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.execute(
                text("UPDATE sys.setting SET value = CAST(:v AS jsonb) WHERE name = :n"),
                {"v": str(per_minute), "n": "sys.requests_per_minute"},
            )
    await owner.dispose()


async def test_too_many_requests_are_refused(upgraded: Settings, sealer: Sealer) -> None:
    await _limit(upgraded, 10)  # a setting administrators change in the app
    try:
        app = _app(upgraded, sealer)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=SITE) as client:
                answers = [await client.get("/api/v1/me") for _ in range(12)]
    finally:
        await _limit(upgraded, 600)
        await installation.refresh(create_engine(upgraded.database_url))
    assert answers[0].status_code == 401
    assert answers[-1].status_code == 429
    assert int(answers[-1].headers["retry-after"]) > 0


# ---- the contract ----


def test_the_openapi_description_is_the_one_in_the_repository(
    upgraded: Settings, sealer: Sealer
) -> None:
    current = _app(upgraded, sealer).openapi()
    committed = json.loads(OPENAPI.read_text()) if OPENAPI.is_file() else None
    assert current == committed, (
        "The API changed: run `uv run python -m p42cloud.web.openapi` and review the difference."
    )
