"""Installing and upgrading modules: the core first, then each module after what it depends on.

Before anything changes, every manifest is checked, every dependency must be there in a version
its range allows, and there must be no cycle. Then each module, in its own transaction (as the
owner): its database role and schema, its tables, its roles, its first records, its
documentation, and its record with the version. A module is never downgraded.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
from p42cloud.services.events.service import register as register_subscriptions
from p42cloud.services.events.service import write_events
from p42cloud.services.jobs.service import register as register_jobs
from p42cloud.services.jobs.service import write_jobs
from p42cloud.services.languages import service as languages
from p42cloud.services.notifications.service import register as register_kinds
from p42cloud.services.notifications.service import send, write_kinds
from p42cloud.services.routes.service import register as register_routes
from p42cloud.services.routes.service import write_routes
from p42cloud.services.states.models import Flow
from p42cloud.services.states.service import write_flows
from p42cloud.ui.actions.models import UIAction
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


def _move_buttons(flows: tuple[Flow, ...]) -> tuple[UIAction, ...]:
    """Each transition as a button on the record's form, where the record is in its state."""
    buttons: list[UIAction] = []
    for flow in flows:
        for t in flow.transitions:
            here: Any = (
                {"field": flow.field, "op": "eq", "value": t.from_state}
                if t.from_state is not None
                else {"field": flow.field, "op": "ne", "value": t.to_state}
            )
            buttons.append(
                UIAction(
                    f"move_{t.name}",
                    flow.table,
                    t.label,
                    places=("form_button", "list_row"),
                    icon=t.icon or "arrow_forward",
                    condition=here if t.condition is None else {"and": [here, t.condition]},
                    confirm=t.confirm,
                    set={flow.field: t.to_state},
                )
            )
    return tuple(buttons)


def compose(modules: list[Module]) -> None:
    """Connects the modules' hooks; every process does it once at start."""
    for module in modules:
        HOOKS.add(*module.hooks)
        register_actions(module.id, module.actions)
        register_jobs(module.id, module.jobs)
        register_types(module.id, module.connection_types)
        register_calls(module.id, module.calls)
        register_subscriptions(module.id, module.subscriptions)
        register_kinds(module.id, module.notification_kinds)
        register_routes(module.id, module.routes)
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
    found = (
        await conn.execute(
            text("SELECT version, publisher FROM sys.module WHERE name = :id"), {"id": module.id}
        )
    ).first()
    installed = None if found is None else found.version
    if found is not None and found.publisher and found.publisher != module.publisher:
        raise _refuse(
            f"{module.id} is installed from the publisher {found.publisher!r}; this package is"
            f" from {module.publisher!r}. A module is updated only by its own publisher."
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
        "publisher": module.publisher,
        # In English here; the translations in force go back on after the install.
        "title": json.dumps({"en": module.name[:80]}),
        "category_title": json.dumps({"en": module.category}),
        "summary": json.dumps({"en": module.description[:255]}) if module.description else None,
    }
    if installed is None:
        await conn.execute(
            text(
                "INSERT INTO sys.module"
                " (name, label, version, depends_on, description, enabled, protected, category,"
                " icon, publisher, title, category_title, summary) VALUES (:id, :label, :version,"
                " CAST(:depends_on AS jsonb), :description, true, :protected, :category, :icon,"
                " :publisher, CAST(:title AS jsonb), CAST(:category_title AS jsonb),"
                " CAST(:summary AS jsonb))"
            ),
            values,
        )
    else:
        await conn.execute(
            text(
                "UPDATE sys.module SET label = :label, version = :version,"
                " depends_on = CAST(:depends_on AS jsonb), description = :description,"
                " protected = :protected, category = :category, icon = :icon, deleted = NULL,"
                " publisher = :publisher, title = CAST(:title AS jsonb),"
                " category_title = CAST(:category_title AS jsonb),"
                " summary = CAST(:summary AS jsonb)"
                " WHERE name = :id"
            ),
            values,
        )
    if installed != module.version:  # a new module's offer goes once it's installed
        await settle_updates(conn, module.id, module.version)


async def settle_updates(conn: AsyncConnection, module: str, version: str | None) -> None:
    """The available updates after a module changed: those its new version reached (or all,
    when it's gone) are retired, newer ones say what's installed now. So the list is right
    before the next check."""
    rows = (
        await conn.execute(
            text(
                "SELECT sys_id, available FROM sys.module_update"
                " WHERE module = :m AND deleted IS NULL"
            ),
            {"m": module},
        )
    ).all()
    for row in rows:
        reached = version is None or Version(str(row.available)) <= Version(version)
        await conn.execute(
            text(
                "UPDATE sys.module_update SET deleted = now() WHERE sys_id = :i"
                if reached
                else "UPDATE sys.module_update SET installed = :v WHERE sys_id = :i"
            ),
            {"i": row.sys_id, "v": version},
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


def _may_take(module: Module, state: State | None) -> None:
    """A module is never downgraded, and a new one says who makes it."""
    if state is not None and state.version > module.parsed_version:
        raise _refuse(
            f"{module.id} {state.version} is installed; {module.version} would go back. Modules"
            " are never downgraded."
        )
    if state is None and not module.publisher:
        raise _refuse(f"{module.id} doesn't say who makes it (Module(publisher=…)).")


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
    upgraded = False
    for module in ordered:
        state = known.get(module.id)
        before = state.version if state is not None else None
        _may_take(module, state)
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
            await write_flows(conn, catalog, module.id, module.flows)
            await write_actions(
                conn, catalog, module.id, (*module.actions, *_move_buttons(module.flows))
            )
            await write_rules(conn, catalog, module.id, module.rules)
            await write_routes(conn, module.id, module.routes, {t.name for t in module.tables})
            await write_widgets(conn, catalog, module.id, module.widgets)
            await write_jobs(conn, module.id, module.jobs, module.schedules)
            await write_types(conn, module.id, module.connection_types)
            await write_kinds(conn, module.id, module.notification_kinds)
            await write_calls(
                conn, module.id, module.calls, {t.name for t in module.connection_types}
            )
            await write_events(conn, module.id, module.events, module.subscriptions)
            await write_navigation(conn, catalog, module.id, module.navigation)
            await write_themes(conn, module.id, module.themes)
            await settings.write(conn, module.id, module.settings)
            # After its record: the module's own name and category are translated too.
            await _record(conn, module, module.id in protected)
            await _ship_translations(conn, module)
            if state is None and module.id != CORE and module.roles:
                await _announce_roles(conn, module)
            if state is not None and (not state.enabled or state.removed):
                await switch(conn, module.id, on=False)  # new tables and roles stay off too
        if before != module.parsed_version or (state is not None and state.removed):
            done.append(f"{module.id} {before or 'new'} → {module.version}")
            upgraded = upgraded or before is not None
    await _finish(engine, upgraded=upgraded)
    return done


async def _finish(engine: AsyncEngine, *, upgraded: bool) -> None:
    """The modules wrote their texts in English: the translations in force go back on. After
    an update, the update check runs soon."""
    async with owner_transaction(engine) as conn:
        await languages.seed(conn)
        await languages.apply(conn, await load_catalog(conn))
        if upgraded:
            await check_updates_soon(conn)


async def check_updates_soon(conn: AsyncConnection, trigger: str = "update") -> None:
    """After an update, what fits may have changed (a module waiting for this core), and a
    new installation doesn't know the registry's modules yet: the update check runs now rather
    than at its daily time."""
    await conn.execute(
        text(
            "INSERT INTO sys.job_run (job, schedule, state, trigger, attempt, not_before)"
            " SELECT 'sys.check_updates', NULL, 'queued', :trigger, 1, now()"
            " WHERE EXISTS (SELECT 1 FROM sys.job WHERE name = 'sys.check_updates'"
            " AND deleted IS NULL) AND NOT EXISTS (SELECT 1 FROM sys.job_run"
            " WHERE job = 'sys.check_updates' AND state = 'queued' AND deleted IS NULL)"
        ),
        {"trigger": trigger},
    )


async def _ship_translations(conn: AsyncConnection, module: Module) -> None:
    """The module's own translations, one file per language."""
    if module.translations is None:
        return
    catalog = await load_catalog(conn)
    for file in sorted(module.translations.glob("*.json")):
        try:
            given = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise _refuse(f"{module.id}: {file.name} can't be read: {error}") from error
        await languages.ship(conn, catalog, module.id, languages.check_language(file.stem), given)


async def _module_id(conn: AsyncConnection, module: str) -> str:
    return str(
        await conn.scalar(text("SELECT sys_id FROM sys.module WHERE name = :m"), {"m": module})
    )


async def _announce_roles(conn: AsyncConnection, module: Module) -> None:
    """Administrators hear which roles a new module brings: nobody holds them yet."""
    names = ", ".join(r.label for r in module.roles)
    await send(
        conn,
        "sys.module_roles",
        f"{module.name} is installed: give its roles to groups",
        roles=("sys.admin",),
        message=(
            f"Its roles: {names}. Nobody holds them yet: on the module, Give its roles to"
            " Administrators, or give the ones people need to groups (People and access,"
            " Groups). Its jobs have theirs already."
        ),
        link=f"/form/sys.module/{await _module_id(conn, module.id)}",
    )


async def _migrate(conn: AsyncConnection, module: Module, before: Version) -> list[str]:
    """The module's data migrations passed on the way from `before` to its version, in order."""
    passed = [m for m in module.migrations if before < Version(m.version) <= module.parsed_version]
    done: list[str] = []
    for migration in sorted(passed, key=lambda m: Version(m.version)):
        await migration.run(conn)
        done.append(f"Moved {module.id}'s data for {migration.version}")
    return done
