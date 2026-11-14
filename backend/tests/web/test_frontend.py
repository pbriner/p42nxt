"""Serving the browser app: its files, its page for app addresses, nothing outside its folder."""

import os
from pathlib import Path

import httpx
import pytest

from p42cloud.kernel.config import Settings
from p42cloud.kernel.secrets import Sealer
from p42cloud.web.app import create_app

SITE = "https://p42.test"


@pytest.fixture
def built(tmp_path: Path) -> Path:
    folder = tmp_path / "browser"
    folder.mkdir()
    (folder / "index.html").write_text("<!doctype html><app-root></app-root>")
    (folder / "main-ABC123.js").write_text("console.log('app')")
    (tmp_path / "secret.txt").write_text("not for you")
    return folder


async def test_the_app_is_served_beside_the_api(upgraded: Settings, built: Path) -> None:
    app = create_app(
        upgraded.model_copy(update={"public_url": SITE}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
        frontend_dir=built,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SITE) as client:
            page = await client.get("/list/hello.greeting")
            script = await client.get("/main-ABC123.js")
            outside = await client.get("/..%2Fsecret.txt")
            api = await client.get("/api/v1/nothing")
            health = await client.get("/api/v1/health")
    assert page.status_code == 200 and "<app-root>" in page.text
    policy = page.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "unsafe-eval" not in policy
    assert "frame-ancestors 'none'" in policy
    assert "immutable" in script.headers["cache-control"]
    assert "not for you" not in outside.text  # the app's page, not the file
    assert (api.status_code, api.json()["code"]) == (404, "not_found")
    assert health.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
