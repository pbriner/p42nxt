"""Installing and upgrading modules: the core first, then each module after what it depends on.

Before anything changes, every manifest is checked, every dependency must be there in a version
its range allows, and there must be no cycle. Then each module, in its own transaction (as the
owner): its database role and schema, its tables, its roles, its first records, its
documentation, and its record with the version. A module is never downgraded.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data import settings
from p42cloud.data.catalog import load_catalog
from p42cloud.data.hooks import HOOKS
from p42cloud.data.metadata import declare
from p42cloud.data.upgrade import upgrade
from p42cloud.kernel.db import ensure_module_role, owner_transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.modules.manifest import CORE, Module
from p42cloud.security.access.roles import write_roles
from p42cloud.services.calls.service import register as register_calls
from p42cloud.services.calls.service import write_calls
from p42cloud.services.connections.service import register as register_types
from p42cloud.services.connections.service import write_types
from p42cloud.services.jobs.service import register as register_jobs
from p42cloud.services.jobs.service import write_jobs
from p42cloud.ui.actions.service import register as register_actions
from p42cloud.ui.actions.service import write_actions
from p42cloud.ui.forms.service import write_forms
from p42cloud.ui.navigation.service import write_navigation
from p42cloud.ui.rules.service import write_rules
from p42cloud.ui.themes.service import write_themes
from p42cloud.ui.views.service import write_views
from p42cloud.ui.widgets.service import write_widgets

MAX_PAGE_BYTES = 1_000_000
MAX_PAGES = 500


def _refuse(message: str, errors: list[FieldError] | None = None) -> Problem:
    return Problem(409, "cannot_install", message, errors=errors or [])


def _unmet(modules: list[Module], by_id: dict[str, Module]) -> list[FieldError]:
    errors: list[FieldError] = []
    for module in modules:
        for other, wanted in module.depends_on.items():
            found = by_id.get(other)
            if found is None:
                errors.append(
                    FieldError(module.id, "missing", f"Needs {other}, which isn't there.")
                )
            elif found.parsed_version not in SpecifierSet(wanted):
                message = f"Needs {other} {wanted}; it's {found.version}."
                errors.append(FieldError(module.id, "wrong_version", message))
    return errors


def in_order(modules: list[Module]) -> list[Module]:
    """The modules checked and ordered so each comes after what it depends on."""
    by_id = {m.id: m for m in modules}
    if len(by_id) != len(modules):
        raise _refuse("A module is there twice.")
    if CORE not in by_id:
        raise _refuse("The core module (sys) is missing.")
    for module in modules:
        module.check()
    errors = _unmet(modules, by_id)
    if errors:
        raise _refuse("Some modules need others that aren't there.", errors)
    ordered: list[Module] = []
    placed: set[str] = set()
    waiting = sorted(modules, key=lambda m: (m.id != CORE, m.id))
    while waiting:
        ready = [m for m in waiting if set(m.depends_on) <= placed]
        if not ready:
            names = ", ".join(m.id for m in waiting)
            raise _refuse(f"These modules depend on each other in a circle: {names}.")
        ordered.append(ready[0])
        placed.add(ready[0].id)
        waiting.remove(ready[0])
    return ordered


def compose(modules: list[Module]) -> None:
    """Connects the modules' hooks; every process does it once at start."""
    for module in modules:
        HOOKS.add(*module.hooks)
        register_actions(module.id, module.actions)
        register_jobs(module.id, module.jobs)
        register_types(module.id, module.connection_types)
        register_calls(module.id, module.calls)
        settings.declare(*module.settings)


def _pages(folder: Path) -> list[tuple[str, str, str]]:
    """(path, title, Markdown) for every page in the folder; the title is its first heading."""
    pages: list[tuple[str, str, str]] = []
    for file in sorted(folder.rglob("*.md"))[:MAX_PAGES]:
        if file.stat().st_size > MAX_PAGE_BYTES or file.is_symlink():
            continue
        body = file.read_text(encoding="utf-8")
        heading = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
        pages.append((file.relative_to(folder).as_posix(), heading or file.stem, body))
    return pages


async def write_docs(conn: AsyncConnection, module: str, folder: Path | None) -> None:
    """The module's documentation pages, as its folder has them; pages it dropped are retired."""
    pages = _pages(folder) if folder is not None else []
    for path, title, body in pages:
        changed = await conn.execute(
            text(
                "UPDATE sys.doc_page SET title = :title, body = :body, deleted = NULL"
                " WHERE module = :module AND path = :path"
            ),
            {"module": module, "path": path, "title": title[:255], "body": body},
        )
        if changed.rowcount == 0:
            await conn.execute(
                text(
                    "INSERT INTO sys.doc_page (module, path, title, body)"
                    " VALUES (:module, :path, :title, :body)"
                ),
                {"module": module, "path": path, "title": title[:255], "body": body},
            )
    await conn.execute(
        text(
            "UPDATE sys.doc_page SET deleted = now() WHERE module = :module"
            " AND deleted IS NULL AND NOT path = ANY(:paths)"
        ),
        {"module": module, "paths": [p for p, _, _ in pages]},
    )


async def _record(conn: AsyncConnection, module: Module, protected: bool) -> None:
    """The module's record: new ones start switched on; a returning one keeps its state."""
    installed = await conn.scalar(
        text("SELECT version FROM sys.module WHERE name = :id"), {"id": module.id}
    )
    values = {
        "id": module.id,
        "label": module.name[:80],
        "version": module.version,
        "depends_on": json.dumps(dict(module.depends_on)),
        "description": module.description[:255] or None,
        "protected": protected,
        "category": module.category,
        "icon": module.icon,
    }
    if installed is None:
        await conn.execute(
            text(
                "INSERT INTO sys.module"
                " (name, label, version, depends_on, description, enabled, protected, category,"
                " icon) VALUES (:id, :label, :version, CAST(:depends_on AS jsonb), :description,"
                " true, :protected, :category, :icon)"
            ),
            values,
        )
    else:
        await conn.execute(
            text(
                "UPDATE sys.module SET label = :label, version = :version,"
                " depends_on = CAST(:depends_on AS jsonb), description = :description,"
                " protected = :protected, category = :category, icon = :icon, deleted = NULL"
                " WHERE name = :id"
            ),
            values,
        )


async def switch(conn: AsyncConnection, module: str, *, on: bool) -> None:
    """Switches a module's tables and roles on or off (its data stays as it is)."""
    params = {"id": module, "off": not on}
    await conn.execute(
        text('UPDATE sys."table" SET switched_off = :off WHERE "schema" = :id'), params
    )
    await conn.execute(
        text("UPDATE sys.role SET switched_off = :off WHERE module = :id AND NOT custom"), params
    )
    await conn.execute(text("UPDATE sys.module SET enabled = NOT :off WHERE name = :id"), params)


@dataclass(frozen=True)
class State:
    version: Version
    enabled: bool
    removed: bool


async def states(engine: AsyncEngine) -> dict[str, State]:
    """Every module installed once, removed ones included (none before the first install).

    Only reads, so it needs no request context.
    """
    async with engine.connect() as conn:
        if not await conn.scalar(text("SELECT to_regclass('sys.module') IS NOT NULL")):
            return {}
        rows = await conn.execute(
            text("SELECT name, version, enabled, deleted IS NOT NULL AS removed FROM sys.module")
        )
        return {r.name: State(Version(r.version), r.enabled, r.removed) for r in rows}


def _core_dependencies(ordered: list[Module]) -> set[str]:
    """The core and every module it depends on, directly or not: never switched off."""
    by_id = {m.id: m for m in ordered}
    found = {CORE}
    waiting = [CORE]
    while waiting:
        for other in by_id[waiting.pop()].depends_on:
            if other not in found:
                found.add(other)
                waiting.append(other)
    return found


async def install_modules(
    engine: AsyncEngine, modules: list[Module], *, bring_back: frozenset[str] = frozenset()
) -> list[str]:
    """Installs or upgrades the core and the modules (as the owner); returns what changed.

    Removed modules stay removed even if their package is still there, unless named in
    `bring_back`: then they return with their data, switched off. Switched-off modules are
    upgraded too, and stay off.
    """
    known = await states(engine)
    present = [
        m for m in modules if not (m.id in known and known[m.id].removed and m.id not in bring_back)
    ]
    ordered = in_order(present)
    compose(ordered)
    protected = _core_dependencies(ordered)
    done: list[str] = []
    for module in ordered:
        state = known.get(module.id)
        before = state.version if state is not None else None
        if before is not None and before > module.parsed_version:
            raise _refuse(
                f"{module.id} {before} is installed; {module.version} would go back. Modules are"
                " never downgraded."
            )
        if module.id == CORE:
            done += await upgrade(engine, list(module.tables))
        async with owner_transaction(engine) as conn:
            if module.id != CORE:
                await ensure_module_role(conn, module.id)
                done += await declare(conn, module.id, list(module.tables))
            catalog = await load_catalog(conn)
            await write_roles(conn, catalog, module.id, list(module.roles))
            if before is not None:
                done += await _migrate(conn, module, before)
            if module.seed is not None:
                await module.seed(conn, catalog)
            await write_docs(conn, module.id, module.docs)
            await write_views(conn, catalog, module.id, module.views)
            await write_forms(conn, catalog, module.id, module.forms)
            await write_actions(conn, catalog, module.id, module.actions)
            await write_rules(conn, catalog, module.id, module.rules)
            await write_widgets(conn, catalog, module.id, module.widgets)
            await write_jobs(conn, module.id, module.jobs, module.schedules)
            await write_types(conn, module.id, module.connection_types)
            await write_calls(
                conn, module.id, module.calls, {t.name for t in module.connection_types}
            )
            await write_navigation(conn, catalog, module.id, module.navigation)
            await write_themes(conn, module.id, module.themes)
            await settings.write(conn, module.id, module.settings)
            await _record(conn, module, module.id in protected)
            if state is not None and (not state.enabled or state.removed):
                await switch(conn, module.id, on=False)  # new tables and roles stay off too
        if before != module.parsed_version or (state is not None and state.removed):
            done.append(f"{module.id} {before or 'new'} → {module.version}")
    return done


async def _migrate(conn: AsyncConnection, module: Module, before: Version) -> list[str]:
    """The module's data migrations passed on the way from `before` to its version, in order."""
    passed = [m for m in module.migrations if before < Version(m.version) <= module.parsed_version]
    done: list[str] = []
    for migration in sorted(passed, key=lambda m: Version(m.version)):
        await migration.run(conn)
        done.append(f"Moved {module.id}'s data for {migration.version}")
    return done
