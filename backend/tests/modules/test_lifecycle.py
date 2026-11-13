"""Switching modules on and off, removing and bringing them back, moving data on upgrades.
Nothing is ever deleted."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declarations import Table
from p42cloud.data.records import Records
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.string import String
from p42cloud.data.undo import undo
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules, states
from p42cloud.modules.lifecycle import remove, switch_off, switch_on
from p42cloud.modules.manifest import Migration, Module
from p42cloud.security.access.roles import Grant, Role
from p42cloud.security.access.service import access_for
from tests.security.helpers import acting, role_id

SYS = {"sys": f">={version('p42cloud')}"}
NAME = {"name": String("Name", max_length=40)}


@dataclass
class Pair:
    base: Module
    app: Module  # depends on base

    def all(self) -> list[Module]:
        return [CORE, self.base, self.app]


@pytest.fixture(scope="module")
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


def _with(module_id: str, depends: dict[str, str], docs: Path | None = None) -> Module:
    return Module(
        id=module_id,
        name=module_id,
        version="1.0.0",
        depends_on=SYS | depends,
        tables=(Table("item", "Item", NAME),),
        roles=(
            Role(f"{module_id}.user", "User", (Grant(f"{module_id}.item", ("read", "create")),)),
        ),
        docs=docs,
    )


@pytest.fixture
async def pair(
    new_module: Callable[[], Awaitable[str]], owner: AsyncEngine, tmp_path: Path
) -> Pair:
    base, app = await new_module(), await new_module()
    (tmp_path / "index.md").write_text("# App\n")
    made = Pair(_with(base, {}), _with(app, {base: ">=1"}, tmp_path))
    await _install(owner, made.all())
    return made


async def _install(
    owner: AsyncEngine, modules: list[Module], bring_back: frozenset[str] = frozenset()
) -> list[str]:
    with working_as(Context(channel="install")):
        return await install_modules(owner, modules, bring_back=bring_back)


async def _as_owner(
    owner: AsyncEngine, action: Callable[[AsyncEngine, str], Awaitable[None]], module: str
) -> None:
    with working_as(Context(channel="cli")):
        await action(owner, module)


async def _rows(owner: AsyncEngine, table: str) -> int:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            return int(await conn.scalar(text(f"SELECT count(*) FROM {table}")) or 0)


async def test_a_switched_off_module_counts_for_nobody(
    pair: Pair, owner: AsyncEngine, engine: AsyncEngine
) -> None:
    app = pair.app.id
    async with acting(engine, None) as records:
        await records.create(f"{app}.item", {"name": "Kept"})
        person = (await records.create("sys.person", {"name": "User"})).sys_id
        group = (await records.create("sys.group", {"name": f"Users {app}"})).sys_id
        await records.create(
            "sys.group_role", {"group": group, "role": await role_id(records, f"{app}.user")}
        )
        await records.create("sys.membership", {"group": group, "person": person})
    with pytest.raises(Problem) as needed:
        await _as_owner(owner, switch_off, pair.base.id)  # the app needs it
    assert "need" in needed.value.title

    await _as_owner(owner, switch_off, app)
    async with acting(engine, None) as records:
        with pytest.raises(Problem) as hidden:
            await records.list(f"{app}.item")  # not even the system sees it
        access = await access_for(records.conn, person)
    assert hidden.value.status == 404
    assert not access.of(f"{app}.item", "read")  # the role counts for nobody
    assert await _rows(owner, f'"{app}"."item"') == 1  # the data stays

    await _as_owner(owner, switch_on, app)
    async with acting(engine, person) as records:
        assert len((await records.list(f"{app}.item")).records) == 1


async def test_a_module_can_not_come_on_before_what_it_needs(
    pair: Pair, owner: AsyncEngine
) -> None:
    await _as_owner(owner, switch_off, pair.app.id)
    await _as_owner(owner, switch_off, pair.base.id)
    with pytest.raises(Problem) as refused:
        await _as_owner(owner, switch_on, pair.app.id)
    assert pair.base.id in refused.value.title


async def test_the_core_is_always_on(owner: AsyncEngine) -> None:
    for action in (switch_off, remove):
        with pytest.raises(Problem) as refused:
            await _as_owner(owner, action, "sys")
        assert "core" in refused.value.title


async def test_removing_retires_and_bringing_back_restores(
    pair: Pair, owner: AsyncEngine, engine: AsyncEngine
) -> None:
    app = pair.app.id
    async with acting(engine, None) as records:
        await records.create(f"{app}.item", {"name": "Survives"})
    with pytest.raises(Problem) as still_on:
        await _as_owner(owner, remove, app)
    assert "off" in still_on.value.title
    with pytest.raises(Problem) as needed:
        await _as_owner(owner, switch_off, pair.base.id)
    assert "need" in needed.value.title

    await _as_owner(owner, switch_off, app)
    await _as_owner(owner, remove, app)
    known = await states(owner)
    assert known[app].removed
    assert await _rows(owner, f'"{app}"."item"') == 1  # never deleted
    assert await _rows(owner, f"sys.doc_page WHERE module = '{app}' AND deleted IS NULL") == 0
    # Upgrading doesn't bring it back, though its package is still there.
    assert not [d for d in await _install(owner, pair.all()) if d.startswith(app)]
    assert (await states(owner))[app].removed

    done = await _install(owner, pair.all(), bring_back=frozenset({app}))
    assert any(d.startswith(app) for d in done)
    back = (await states(owner))[app]
    assert (back.removed, back.enabled) == (False, False)  # back, and off until switched on
    await _as_owner(owner, switch_on, app)
    async with acting(engine, None) as records:
        assert [r.values["name"] for r in (await records.list(f"{app}.item")).records] == [
            "Survives"
        ]
    assert await _rows(owner, f"sys.doc_page WHERE module = '{app}' AND deleted IS NULL") == 1


async def test_a_module_needed_by_an_installed_one_stays(pair: Pair, owner: AsyncEngine) -> None:
    await _as_owner(owner, switch_off, pair.app.id)
    await _as_owner(owner, switch_off, pair.base.id)
    with pytest.raises(Problem) as refused:
        await _as_owner(owner, remove, pair.base.id)
    assert pair.app.id in refused.value.title


async def test_a_switched_off_module_is_upgraded_and_stays_off(
    pair: Pair, owner: AsyncEngine, engine: AsyncEngine
) -> None:
    await _as_owner(owner, switch_off, pair.app.id)
    newer = replace(
        pair.app,
        version="1.1.0",
        tables=(*pair.app.tables, Table("extra", "Extra", NAME)),
    )
    done = await _install(owner, [CORE, pair.base, newer])
    assert f"{pair.app.id} 1.0.0 → 1.1.0" in done
    async with acting(engine, None) as records:
        with pytest.raises(Problem):
            await records.list(f"{pair.app.id}.extra")  # new tables come in off, too


async def test_data_moves_once_on_the_upgrade_that_passes_its_version(
    pair: Pair, owner: AsyncEngine, engine: AsyncEngine
) -> None:
    app = pair.app.id
    async with acting(engine, None) as records:
        await records.create(f"{app}.item", {"name": "old style"})
    runs: list[str] = []

    async def capitalize(conn: AsyncConnection) -> None:
        runs.append("ran")
        await conn.execute(text(f'UPDATE "{app}"."item" SET title = initcap(name)'))

    titled = Table("item", "Item", NAME | {"title": String("Title", max_length=40)})
    newer = replace(
        pair.app, version="1.1.0", tables=(titled,), migrations=(Migration("1.1.0", capitalize),)
    )
    done = await _install(owner, [CORE, pair.base, newer])
    await _install(owner, [CORE, pair.base, newer])  # the same version again: nothing moves
    async with acting(engine, None) as records:
        titles = [r.values["title"] for r in (await records.list(f"{app}.item")).records]
    assert runs == ["ran"]
    assert f"Moved {app}'s data for 1.1.0" in done
    assert titles == ["Old Style"]


async def test_a_new_required_field_fills_records_already_there(
    pair: Pair, owner: AsyncEngine, engine: AsyncEngine
) -> None:
    app = pair.app.id
    async with acting(engine, None) as records:
        await records.create(f"{app}.item", {"name": "Before"})
    flagged = Table("item", "Item", NAME | {"done": Boolean("Done", required=True, default=False)})
    await _install(owner, [CORE, pair.base, replace(pair.app, version="1.0.1", tables=(flagged,))])
    async with acting(engine, None) as records:
        found = await records.list(f"{app}.item")
    assert [r.values["done"] for r in found.records] == [False]


async def test_the_core_and_what_it_needs_are_protected(
    owner: AsyncEngine, engine: AsyncEngine
) -> None:
    async with acting(engine, None) as records:
        page = await records.list("sys.module", where={"field": "name", "op": "eq", "value": "sys"})
    assert page.records[0].values["protected"] is True


async def test_undo_skips_what_a_switched_off_module_holds(
    pair: Pair, owner: AsyncEngine, engine: AsyncEngine
) -> None:
    app = pair.app.id
    context = Context(channel="ui", access=EVERYTHING)
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            await records.create(f"{app}.item", {"name": "Made"})
    await _as_owner(owner, switch_off, app)
    async with acting(engine, None) as records:
        result = await undo(records.conn, records.catalog, request=context.request_id)
    assert result.changed == []
    assert [s.reason for s in result.skipped] == ["This table's changes can't be undone here."]
