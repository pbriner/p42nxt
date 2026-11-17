"""Installing and upgrading modules: checked first, in order, each with its role, tables, roles,
documentation and record; never downgraded."""

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from p42cloud.data.declarations import Table
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.safety import problems
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from p42cloud.modules.registry import discover
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.access.roles import Grant, Role
from tests.conftest import ADMIN_URL
from tests.security.helpers import acting


@pytest.fixture(scope="module")
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="module")
async def app(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


async def _install(owner: AsyncEngine, *modules: Module) -> list[str]:
    with working_as(Context(channel="install")):
        return await install_modules(owner, [CORE, *modules])


async def _refuse_names(change: Change) -> None:
    if change.values.get("name") == "forbidden":
        raise Problem(422, "validation", "Not that name.")


def _module(module_id: str, docs: Path, **changes: object) -> Module:
    item = Table("item", "Item", {"name": String("Name", max_length=40)})
    made = Module(
        id=module_id,
        publisher="test",
        name="Example",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(item,),
        roles=(Role(f"{module_id}.reader", "Reader", (Grant(f"{module_id}.item", ("read",)),)),),
        hooks=(TableHook(f"{module_id}.item", before=_refuse_names),),
        docs=docs,
    )
    return replace(made, **changes)  # type: ignore[arg-type]


def _docs(folder: Path, pages: dict[str, str]) -> Path:
    for path, body in pages.items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(body)
    return folder


async def _pages(app: AsyncEngine, module: str) -> dict[str, str]:
    async with acting(app, None) as records:
        page = await records.list(
            "sys.doc_page", where={"field": "module", "op": "eq", "value": module}, limit=500
        )
    return {r.values["path"]: r.values["title"] for r in page.records}


async def _record(app: AsyncEngine, module: str) -> dict[str, object]:
    async with acting(app, None) as records:
        page = await records.list(
            "sys.module", where={"field": "name", "op": "eq", "value": module}
        )
    return page.records[0].values


async def test_the_core_is_a_module_with_its_documentation(app: AsyncEngine) -> None:
    core = await _record(app, "sys")
    pages = await _pages(app, "sys")
    assert core["version"] == version("p42cloud")
    assert pages["records.md"] == "Records"
    assert pages["access.md"] == "Access"


async def test_a_module_installs_and_upgrades(
    owner: AsyncEngine,
    app: AsyncEngine,
    new_module: Callable[[], Awaitable[str]],
    tmp_path: Path,
) -> None:
    module_id = await new_module()
    docs = _docs(tmp_path, {"index.md": "# Example\nHello.", "guide/start.md": "# Starting\n"})
    done = await _install(owner, _module(module_id, docs))
    assert f"{module_id} new → 1.0.0" in done
    assert await _pages(app, module_id) == {"index.md": "Example", "guide/start.md": "Starting"}
    async with acting(app, None) as records:
        await records.create(f"{module_id}.item", {"name": "First"})
        with pytest.raises(Problem):  # the module's hook runs
            await records.create(f"{module_id}.item", {"name": "forbidden"})
        role = await records.list(
            "sys.role", where={"field": "module", "op": "eq", "value": module_id}
        )
    assert [r.values["name"] for r in role.records] == [f"{module_id}.reader"]

    (docs / "guide" / "start.md").unlink()
    bigger = Table(
        "item",
        "Item",
        {"name": String("Name", max_length=40), "note": String("Note", max_length=80)},
    )
    done = await _install(owner, _module(module_id, docs, version="1.1.0", tables=(bigger,)))
    assert f"{module_id} 1.0.0 → 1.1.0" in done
    assert f"Add {module_id}.item.note" in done
    assert await _pages(app, module_id) == {"index.md": "Example"}  # the dropped page is retired
    assert (await _record(app, module_id))["version"] == "1.1.0"
    assert await _install(owner, _module(module_id, docs, version="1.1.0", tables=(bigger,))) == []

    with pytest.raises(Problem) as back:
        await _install(owner, _module(module_id, docs, version="1.0.5", tables=(bigger,)))
    assert "never downgraded" in back.value.title


async def test_the_owner_makes_a_new_modules_role(
    owner: AsyncEngine, app: AsyncEngine, database: str, tmp_path: Path
) -> None:
    module_id = f"r{uuid.uuid4().hex[:8]}"  # no role made beforehand
    try:
        await _install(owner, _module(module_id, _docs(tmp_path, {})))
        async with acting(app, None) as records:
            made = await records.create(f"{module_id}.item", {"name": "Works"})
        assert made.values["name"] == "Works"
        assert await problems(app) == []  # still a safe database
    finally:
        admin = create_async_engine(
            ADMIN_URL.rsplit("/", 1)[0] + f"/{database}", isolation_level="AUTOCOMMIT"
        )
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP OWNED BY "p42_mod_{module_id}"'))
            await conn.execute(text(f'DROP ROLE "p42_mod_{module_id}"'))
        await admin.dispose()


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"depends_on": {"sys": ">=0.1", "nowhere": ">=1"}}, "missing"),
        ({"depends_on": {"sys": ">=99"}}, "wrong_version"),
        ({"depends_on": {}}, "needs_core"),
        ({"version": "one"}, "bad_version"),
        ({"hooks": (TableHook("sys.person", before=_refuse_names),)}, "not_its_table"),
    ],
)
async def test_modules_are_checked_before_anything_changes(
    owner: AsyncEngine, tmp_path: Path, changes: dict[str, object], code: str
) -> None:
    module = _module(f"c{uuid.uuid4().hex[:8]}", tmp_path, **changes)
    with pytest.raises(Problem) as refused:
        await _install(owner, module)
    assert code in [e.code for e in refused.value.errors]


async def test_modules_depending_on_each_other_in_a_circle_are_refused(
    owner: AsyncEngine, tmp_path: Path
) -> None:
    first, second = f"a{uuid.uuid4().hex[:8]}", f"b{uuid.uuid4().hex[:8]}"
    core = {"sys": ">=0.1"}
    with pytest.raises(Problem) as refused:
        await _install(
            owner,
            _module(first, tmp_path, depends_on=core | {second: ">=1"}),
            _module(second, tmp_path, depends_on=core | {first: ">=1"}),
        )
    assert "circle" in refused.value.title


def test_module_packages_are_found() -> None:
    assert [m.id for m in discover()] == ["costing", "dca", "finance", "hello", "partners"]


async def test_everyone_reads_the_documentation(app: AsyncEngine) -> None:
    async with acting(app, None) as records:
        person = await records.create("sys.person", {"name": f"Reader {uuid.uuid4().hex[:6]}"})
    async with acting(app, person.sys_id) as records:
        pages = await records.list("sys.doc_page", limit=500)
        modules = await records.list("sys.module")
    assert any(r.values["path"] == "records.md" for r in pages.records)
    assert {"name", "version"} <= modules.records[0].values.keys()
    assert "depends_on" not in modules.records[0].values


async def test_administrators_hear_which_roles_a_new_module_brings(
    owner: AsyncEngine,
    app: AsyncEngine,
    new_module: Callable[[], Awaitable[str]],
    tmp_path: Path,
) -> None:
    module_id = await new_module()
    async with acting(app, None) as records:
        admin = await records.create("sys.person", {"name": f"Admin of {module_id}"})
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin.sys_id})
    async with acting(app, None) as records:
        admins = await records.conn.scalar(
            text(
                "SELECT count(DISTINCT m.person) FROM sys.membership m"
                ' JOIN sys.group_role gr ON gr."group" = m."group" AND gr.deleted IS NULL'
                " JOIN sys.role r ON r.sys_id = gr.role AND r.name = 'sys.admin'"
                " JOIN sys.person p ON p.sys_id = m.person AND p.active AND p.kind <> 'service'"
                " WHERE m.deleted IS NULL"
            )
        )
    await _install(owner, _module(module_id, _docs(tmp_path, {"index.md": "# X\n"})))
    await _install(
        owner, _module(module_id, _docs(tmp_path, {"index.md": "# X\n"}), version="1.0.1")
    )
    async with acting(app, None) as records:
        told = list(
            await records.conn.execute(
                text(
                    "SELECT title, message, link FROM sys.notification"
                    " WHERE kind = 'sys.module_roles' AND link LIKE :l"
                ),
                {"l": f"%{module_id}"},
            )
        )
    assert admins and len(told) == admins  # once each, on the install only, not the upgrade
    assert "give its roles to groups" in told[0].title
    assert "Reader" in told[0].message


async def test_a_module_from_before_publishers_keeps_loading_and_takes_one_later(
    owner: AsyncEngine,
    app: AsyncEngine,
    new_module: Callable[[], Awaitable[str]],
    tmp_path: Path,
) -> None:
    module_id = await new_module()
    docs = _docs(tmp_path, {"index.md": "# Example\n"})
    with pytest.raises(Problem) as nobody:
        await _install(owner, _module(module_id, docs, publisher=""))
    assert "who makes it" in nobody.value.title
    await _install(owner, _module(module_id, docs))
    # As installed before publishers: no publisher on record, nor in its manifest.
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.execute(
                text("UPDATE sys.module SET publisher = NULL WHERE name = :m"), {"m": module_id}
            )
    assert await _install(owner, _module(module_id, docs, publisher="")) == []  # still loads
    await _install(owner, _module(module_id, docs, version="1.0.1"))
    assert (await _record(app, module_id))["publisher"] == "test"
    with pytest.raises(Problem) as taken:
        await _install(owner, _module(module_id, docs, version="1.0.2", publisher="other"))
    assert "only by its own publisher" in taken.value.title


async def test_an_update_checks_for_updates_again_soon(
    owner: AsyncEngine,
    app: AsyncEngine,
    new_module: Callable[[], Awaitable[str]],
    tmp_path: Path,
) -> None:
    module_id = await new_module()
    docs = _docs(tmp_path, {"index.md": "# Example\n"})

    async def queued() -> int:
        async with acting(app, None) as records:
            found = await records.conn.scalar(
                text(
                    "SELECT count(*) FROM sys.job_run WHERE job = 'sys.check_updates'"
                    " AND state = 'queued' AND trigger = 'update'"
                )
            )
            await records.conn.execute(
                text(
                    "UPDATE sys.job_run SET state = 'skipped' WHERE job = 'sys.check_updates'"
                    " AND state = 'queued'"
                )
            )
        return int(found or 0)

    await queued()  # earlier tests' runs out of the way
    await _install(owner, _module(module_id, docs))
    assert await queued() == 0  # a new module: nothing installed changed
    await _install(owner, _module(module_id, docs, version="1.0.1"))
    await _install(owner, _module(module_id, docs, version="1.0.2"))
    assert await queued() == 1  # once, however many updates came before the worker
