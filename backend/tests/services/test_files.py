"""Files attached to records, over the API: the record's rights, checked kinds, a size limit,
sealed at rest, served so they can't run."""

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
from p42cloud.security.access.roles import Grant, Role
from p42cloud.security.tokens.service import make_token
from p42cloud.services.files.service import clean_name, kind_of
from p42cloud.web.app import create_app
from tests.security.helpers import acting, change

SITE = "https://p42.test"
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << >>\n%%EOF\n"
ALL = ("read", "create", "write", "delete", "restore")
MINE = {"field": "created_by", "op": "eq", "value": "@me"}


def _module(module_id: str) -> Module:
    receipt = Table("receipt", "Receipt", {"text": String("Text", max_length=60)}, files=True)
    plain = Table("plain", "Plain", {"text": String("Text", max_length=60)})
    return Module(
        id=module_id,
        publisher="test",
        name="Files",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(receipt, plain),
        roles=(
            Role(
                f"{module_id}.keeper",
                "Keeper",
                (
                    Grant(f"{module_id}.receipt", ALL, condition=MINE),
                    Grant(f"{module_id}.plain", ALL),
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
    """Ann and Ben keep receipts (each their own), Rea may only read them; one API client."""
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


async def test_a_file_is_attached_read_and_removed_with_the_records_rights(
    setup: dict[str, Any], app_engine: AsyncEngine
) -> None:
    api: httpx.AsyncClient = setup["api"]
    receipt = await _receipt(setup, "ann")
    files = f"/api/v1/{setup['module']}/receipt/{receipt}/files"
    added = await api.post(
        files, params={"name": "../Heating oil.pdf"}, content=PDF, headers=setup["ann"]
    )
    assert added.status_code == 201, added.text
    attached = added.json()
    assert (attached["name"], attached["type"], attached["size"]) == (
        "Heating oil.pdf",
        "application/pdf",
        len(PDF),
    )
    listed = await api.get(files, headers=setup["ann"])
    assert [f["name"] for f in listed.json()["files"]] == ["Heating oil.pdf"]

    got = await api.get(f"/api/v1/files/{attached['sys_id']}", headers=setup["ann"])
    assert got.content == PDF
    assert got.headers["content-disposition"].startswith("attachment;")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]
    shown = await api.get(
        f"/api/v1/files/{attached['sys_id']}", params={"inline": "true"}, headers=setup["ann"]
    )
    assert shown.headers["content-disposition"].startswith("inline;")

    # Sealed at rest: the database doesn't hold the file as it is.
    async with acting(app_engine, None) as records:
        sealed = await records.conn.scalar(
            text(
                "SELECT d.sealed FROM sys.file f JOIN sys.file_data d"
                " ON d.id = CAST(f.content AS uuid) WHERE f.sys_id = :id"
            ),
            {"id": uuid.UUID(attached["sys_id"])},
        )
    assert sealed is not None and "%PDF" not in str(sealed)

    # Ben may not read Ann's receipt: neither its files nor any file of it.
    assert (await api.get(files, headers=setup["ben"])).status_code == 404
    other = await api.get(f"/api/v1/files/{attached['sys_id']}", headers=setup["ben"])
    assert other.status_code == 404
    taken = await api.post(files, params={"name": "x.pdf"}, content=PDF, headers=setup["ben"])
    assert taken.status_code == 404
    assert (
        await api.delete(f"/api/v1/files/{attached['sys_id']}", headers=setup["ben"])
    ).status_code == 404

    removed = await api.delete(f"/api/v1/files/{attached['sys_id']}", headers=setup["ann"])
    assert removed.status_code == 204
    assert (await api.get(files, headers=setup["ann"])).json()["files"] == []


async def test_reading_a_record_doesnt_let_one_attach_to_it(
    setup: dict[str, Any], app_engine: AsyncEngine
) -> None:
    api: httpx.AsyncClient = setup["api"]
    receipt = await _receipt(setup, "ann")
    files = f"/api/v1/{setup['module']}/receipt/{receipt}/files"
    await api.post(files, params={"name": "a.pdf"}, content=PDF, headers=setup["ann"])
    listed = await api.get(files, headers=setup["rea"])  # Rea reads every receipt
    assert [f["name"] for f in listed.json()["files"]] == ["a.pdf"]
    refused = await api.post(files, params={"name": "b.pdf"}, content=PDF, headers=setup["rea"])
    assert refused.status_code == 403


async def test_only_accepted_kinds_and_sizes_are_attached(
    setup: dict[str, Any], app_engine: AsyncEngine
) -> None:
    api: httpx.AsyncClient = setup["api"]
    receipt = await _receipt(setup, "ann")
    files = f"/api/v1/{setup['module']}/receipt/{receipt}/files"
    page = b"<html><script>alert(1)</script></html>"
    for name, content in (("page.html", page), ("page.pdf", page), ("x.svg", b"<svg/>")):
        refused = await api.post(
            files, params={"name": name}, content=content, headers=setup["ann"]
        )
        assert refused.status_code == 415, name
    empty = await api.post(files, params={"name": "e.pdf"}, content=b"", headers=setup["ann"])
    assert empty.status_code == 422
    plain = await _receipt(setup, "ann", "plain")
    no_files = await api.post(
        f"/api/v1/{setup['module']}/plain/{plain}/files",
        params={"name": "a.pdf"},
        content=PDF,
        headers=setup["ann"],
    )
    assert no_files.status_code == 404
    async with acting(app_engine, None) as records:
        found = await records.list(
            "sys.setting", where={"field": "name", "op": "eq", "value": "sys.file_max_mb"}
        )
        setting = found.records[0].sys_id
        before = found.records[0].values["value"]
        await change(records, "sys.setting", setting, {"value": 1})
    try:
        big = PDF + b"0" * (1024 * 1024)
        too_large = await api.post(
            files, params={"name": "big.pdf"}, content=big, headers=setup["ann"]
        )
        assert too_large.status_code == 413
    finally:
        async with acting(app_engine, None) as records:
            await change(records, "sys.setting", setting, {"value": before})


@pytest.mark.parametrize(
    ("data", "name", "kind"),
    [
        (PDF, "x.pdf", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n....", "x.png", "image/png"),
        (b"\xff\xd8\xff\xe0....", "photo", "image/jpeg"),
        (b"a;b\n1;2\n", "costs.csv", "text/csv"),
        (b"a;b\x00", "costs.csv", None),
        (b"PK\x03\x04 not a zip", "x.xlsx", None),
        (b"MZ\x90\x00", "setup.exe", None),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "x.txt", "text/plain"),
    ],
)
def test_a_file_is_known_by_its_content(data: bytes, name: str, kind: str | None) -> None:
    assert kind_of(data, name) == kind


def test_names_lose_folders_and_unsafe_characters() -> None:
    assert clean_name("C:\\Users\\ann\\inv<1>.pdf") == "inv_1_.pdf"
    assert clean_name("../../etc/passwd") == "passwd"
    assert len(clean_name("a" * 300 + ".pdf")) == 200
    assert clean_name("a" * 300 + ".pdf").endswith(".pdf")
