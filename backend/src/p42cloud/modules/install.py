"""Installing and upgrading modules: the core first, then each module after what it depends on.

Before anything changes, every manifest is checked, every dependency must be there in a version
its range allows, and there must be no cycle. Then each module, in its own transaction (as the
owner): its database role and schema, its tables, its roles, its first records, its
documentation, and its record with the version. A module is never downgraded.
"""

import json
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.hooks import HOOKS
from p42cloud.data.metadata import declare
from p42cloud.data.upgrade import upgrade
from p42cloud.kernel.db import APP_ROLE, module_role, owner_transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.modules.manifest import CORE, Module
from p42cloud.security.access.roles import write_roles

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


async def ensure_role(conn: AsyncConnection, module: str) -> None:
    """The module's database role, and the app's right to switch to it."""
    role = module_role(module)  # checked, so it's safe in the statement
    exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})
    if not exists:
        await conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT'))
    member = await conn.scalar(
        text(
            "SELECT 1 FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid"
            " JOIN pg_roles u ON u.oid = m.member WHERE g.rolname = :r AND u.rolname = :app"
        ),
        {"r": role, "app": APP_ROLE},
    )
    if not member:
        await conn.execute(text(f'GRANT "{role}" TO {APP_ROLE} WITH INHERIT FALSE, SET TRUE'))


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


async def _record(conn: AsyncConnection, module: Module) -> None:
    installed = await conn.scalar(
        text("SELECT version FROM sys.module WHERE name = :id"), {"id": module.id}
    )
    values = {
        "id": module.id,
        "label": module.name[:80],
        "version": module.version,
        "depends_on": json.dumps(dict(module.depends_on)),
        "description": module.description[:255] or None,
    }
    if installed is None:
        await conn.execute(
            text(
                "INSERT INTO sys.module (name, label, version, depends_on, description)"
                " VALUES (:id, :label, :version, CAST(:depends_on AS jsonb), :description)"
            ),
            values,
        )
    else:
        await conn.execute(
            text(
                "UPDATE sys.module SET label = :label, version = :version,"
                " depends_on = CAST(:depends_on AS jsonb), description = :description,"
                " deleted = NULL WHERE name = :id"
            ),
            values,
        )


async def _installed_version(engine: AsyncEngine, module: str) -> Version | None:
    """The installed version, if the module table exists yet (not before the first install)."""
    async with owner_transaction(engine) as conn:
        if not await conn.scalar(text("SELECT to_regclass('sys.module') IS NOT NULL")):
            return None
        found = await conn.scalar(
            text("SELECT version FROM sys.module WHERE name = :id AND deleted IS NULL"),
            {"id": module},
        )
    return Version(found) if found else None


async def install_modules(engine: AsyncEngine, modules: list[Module]) -> list[str]:
    """Installs or upgrades the core and the modules (as the owner); returns what changed."""
    ordered = in_order(modules)
    compose(ordered)
    done: list[str] = []
    for module in ordered:
        before = await _installed_version(engine, module.id)
        if before is not None and before > module.parsed_version:
            raise _refuse(
                f"{module.id} {before} is installed; {module.version} would go back. Modules are"
                " never downgraded."
            )
        if module.id == CORE:
            done += await upgrade(engine, list(module.tables))
        async with owner_transaction(engine) as conn:
            if module.id != CORE:
                await ensure_role(conn, module.id)
                done += await declare(conn, module.id, list(module.tables))
            catalog = await load_catalog(conn)
            await write_roles(conn, catalog, module.id, list(module.roles))
            if module.seed is not None:
                await module.seed(conn, catalog)
            await write_docs(conn, module.id, module.docs)
            await _record(conn, module)
        if before != module.parsed_version:
            done.append(f"{module.id} {before or 'new'} → {module.version}")
    return done
