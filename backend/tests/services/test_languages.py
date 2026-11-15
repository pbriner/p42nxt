"""Translations of what modules define: exported, imported all or nothing, put on the records,
kept over module updates, outdated when the English changes."""

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.modules.core import CORE
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.tokens.service import make_token
from p42cloud.services.languages import service as languages
from p42cloud.web.app import create_app
from tests.security.helpers import acting

LANG = "fr"  # one no test elsewhere uses
KEY = "sys.table|sys.person|label"
FIELD = "sys.field|sys.person.name|label"


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


@pytest.fixture
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.owner_database_url)
    yield made
    await made.dispose()


@pytest.fixture(autouse=True)
async def clean(owner: AsyncEngine) -> AsyncGenerator[None]:
    yield
    async with _owner(owner) as conn:
        await conn.execute(
            text("UPDATE sys.translation SET deleted = now() WHERE language = :l"), {"l": LANG}
        )
        await languages.apply(conn, await load_catalog(conn))


@asynccontextmanager
async def _owner(owner: AsyncEngine) -> AsyncGenerator[AsyncConnection]:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            yield conn


async def _label(owner: AsyncEngine, name: str = "person") -> dict[str, str]:
    async with _owner(owner) as conn:
        found = await conn.scalar(
            text("SELECT label FROM sys.\"table\" WHERE schema = 'sys' AND name = :n"),
            {"n": name},
        )
    return dict(found)


async def _apply(owner: AsyncEngine) -> None:
    async with _owner(owner) as conn:
        await languages.apply(conn, await load_catalog(conn))


async def test_the_export_lists_every_text_in_english(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        out = await languages.export(records.conn, records.catalog, LANG, "sys")
    by = {t["key"]: t for t in out["texts"]}
    assert by[KEY]["english"] == "Person" and by[KEY]["state"] == "missing"
    assert by[FIELD]["text"] == ""
    assert [t["key"] for t in out["texts"]] == sorted(by)


async def test_an_import_is_checked_all_or_nothing(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        for texts, code in (
            ([{"key": KEY, "text": "Personne"}, {"key": "nope|x|label", "text": "x"}], "unknown"),
            ([{"key": KEY, "text": "x" * 2001}], "invalid"),
        ):
            with pytest.raises(Problem) as refused:
                await languages.import_texts(
                    records.conn, records.catalog, LANG, {"language": LANG, "texts": texts}
                )
            assert code in {e.code for e in refused.value.errors}
        with pytest.raises(Problem):
            await languages.import_texts(
                records.conn, records.catalog, LANG, {"language": "de", "texts": []}
            )
        kept = await records.conn.scalar(
            text("SELECT count(*) FROM sys.translation WHERE language = :l AND deleted IS NULL"),
            {"l": LANG},
        )
    assert kept == 0


async def test_placeholders_must_stay() -> None:
    known = {"k": languages.Source("k", None, "{n} records")}
    with pytest.raises(Problem) as refused:
        languages._checked({"texts": [{"key": "k", "text": "enregistrements"}]}, known, LANG)  # pyright: ignore[reportPrivateUsage]
    assert refused.value.errors[0].code == "placeholders"
    ok = languages._checked({"texts": [{"key": "k", "text": "{n} fiches"}]}, known, LANG)  # pyright: ignore[reportPrivateUsage]
    assert ok[0][1] == "{n} fiches"


async def test_imported_texts_show_and_outlive_module_updates(
    engine: AsyncEngine, owner: AsyncEngine
) -> None:
    async with acting(engine, None) as records:
        counts = await languages.import_texts(
            records.conn,
            records.catalog,
            LANG,
            {"language": LANG, "texts": [{"key": KEY, "text": "Personne"}]},
        )
    assert counts["added"] == 1
    await _apply(owner)
    assert (await _label(owner))[LANG] == "Personne"
    # A module update writes its English again; the translation goes back on.
    async with _owner(owner) as conn:
        await conn.execute(
            text(
                'UPDATE sys."table" SET label = \'{"en": "Person"}\''
                " WHERE schema = 'sys' AND name = 'person'"
            )
        )
        await languages.apply(conn, await load_catalog(conn))
    assert (await _label(owner))[LANG] == "Personne"
    # The English changes: outdated, English shows until it's translated again.
    async with _owner(owner) as conn:
        await conn.execute(
            text(
                'UPDATE sys."table" SET label = \'{"en": "Human", "fr": "Personne"}\''
                " WHERE schema = 'sys' AND name = 'person'"
            )
        )
        await languages.apply(conn, await load_catalog(conn))
        outdated = await conn.scalar(
            text("SELECT label FROM sys.\"table\" WHERE schema = 'sys' AND name = 'person'")
        )
        out = await languages.export(conn, await load_catalog(conn), LANG, "sys")
        await conn.execute(
            text(
                'UPDATE sys."table" SET label = \'{"en": "Person"}\''
                " WHERE schema = 'sys' AND name = 'person'"
            )
        )
    assert outdated == {"en": "Human"}  # the old translation is off: English shows
    assert {t["key"]: t["state"] for t in out["texts"]}[KEY] == "outdated"


async def test_the_installations_text_wins_and_an_empty_one_removes_it(
    engine: AsyncEngine, owner: AsyncEngine
) -> None:
    async with _owner(owner) as conn:
        catalog = await load_catalog(conn)
        shipped = await languages.ship(
            conn, catalog, "sys", LANG, {"texts": [{"key": KEY, "text": "Individu"}]}
        )
        await languages.apply(conn, catalog)
    assert shipped == 1
    assert (await _label(owner))[LANG] == "Individu"
    async with acting(engine, None) as records:
        await languages.import_texts(
            records.conn, records.catalog, LANG, {"texts": [{"key": KEY, "text": "Personne"}]}
        )
    await _apply(owner)
    assert (await _label(owner))[LANG] == "Personne"
    async with acting(engine, None) as records:
        counts = await languages.import_texts(
            records.conn, records.catalog, LANG, {"texts": [{"key": KEY, "text": ""}]}
        )
    await _apply(owner)
    assert counts["removed"] == 1
    assert (await _label(owner))[LANG] == "Individu"  # the module's again


async def test_only_administrators_exchange_translations(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        plain = await records.create("sys.person", {"name": f"Plain {uuid.uuid4().hex[:6]}"})
    async with acting(engine, plain.sys_id) as records:
        assert not records.access.of("sys.translation", "read")
        assert records.access.of("sys.language", "read")
        with pytest.raises(Problem):
            await records.create(
                "sys.translation",
                {"language": LANG, "key": KEY, "text": "x", "english": "Person"},
            )


async def test_over_http_administrators_export_and_import(
    engine: AsyncEngine, owner: AsyncEngine, upgraded: Settings
) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        admin = await records.create("sys.person", {"name": f"Translator {tag}"})
        plain = await records.create("sys.person", {"name": f"Reader {tag}"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin.sys_id})
        token = (await make_token(records.conn, admin.sys_id, "Languages")).token
        other = (await make_token(records.conn, plain.sys_id, "Languages")).token
    app = create_app(
        upgraded.model_copy(update={"public_url": "https://p42.test"}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://p42.test") as client:
            listed = await client.get("/api/v1/languages")
            out = await client.get(f"/api/v1/languages/{LANG}/export?module=sys", headers=headers)
            refused = await client.get(
                f"/api/v1/languages/{LANG}/export", headers={"Authorization": f"Bearer {other}"}
            )
            body = out.json()
            for t in body["texts"]:
                if t["key"] == KEY:
                    t["text"] = "Personne"
            done = await client.post(f"/api/v1/languages/{LANG}/import", json=body, headers=headers)
    assert {lang["code"] for lang in listed.json()["languages"]} >= {"en", "de"}
    assert out.status_code == 200 and refused.status_code == 403
    assert done.status_code == 200, done.text
    assert done.json()["added"] == 1 and done.json()["shown"] >= 1
    assert (await _label(owner))[LANG] == "Personne"


@pytest.mark.parametrize("module", ["sys", "dca"])
async def test_german_is_complete_for_the_core_and_dca(owner: AsyncEngine, module: str) -> None:
    async with _owner(owner) as conn:
        installed = await conn.scalar(
            text("SELECT true FROM sys.module WHERE name = :m AND deleted IS NULL"),
            {"m": module},
        )
        if not installed:
            pytest.skip(f"{module} isn't installed in the test database")
        out = await languages.export(conn, await load_catalog(conn), "de", module)
    # Other tests register jobs and calls in the core's name: only what it declares counts.
    declared = {f"sys.{j.name}" for j in CORE.jobs} | {f"sys.{c.name}" for c in CORE.calls}
    left = [
        t["key"]
        for t in out["texts"]
        if t["state"] != "done"
        and not (
            t["key"].split("|")[0] in ("sys.job", "sys.call_type")
            and t["key"].split("|")[1] not in declared
        )
    ]
    assert left == [], f"Translate these in {module}'s translations/de.json: {left[:20]}"
