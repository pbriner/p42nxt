"""Switching modules on and off, and removing them. Nothing is ever deleted.

- **Switched off**: the module's tables and roles count for nobody, the system included: its
  records can't be read or changed, its grants give nothing, its hooks never run. Its data and
  records stay, and it keeps being upgraded.
- **Removed**: it must be off first. Its tables, roles and documentation are retired; the data
  stays, hidden and read-only. Upgrading no longer installs it, even if its package is still
  there; installing it again brings it back with its data, still off.
- **Protected**: the core and the modules it depends on are never switched off or removed. A
  module can't be switched off while a module that's on depends on it, nor removed while one
  still installed does, nor switched on while something it needs is off.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.metadata import write_declarations
from p42cloud.kernel.db import owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.install import settle_updates, switch, write_docs
from p42cloud.security.access.roles import write_roles

_MODULES = text("SELECT name, enabled, protected, depends_on FROM sys.module WHERE deleted IS NULL")


def _refuse(message: str) -> Problem:
    return Problem(409, "module_state", message)


async def _modules(conn: AsyncConnection) -> dict[str, Any]:
    return {row.name: row for row in await conn.execute(_MODULES)}


def _needing(modules: dict[str, Any], module: str, *, enabled_only: bool) -> list[str]:
    return sorted(
        name
        for name, row in modules.items()
        if module in (row.depends_on or {}) and (row.enabled or not enabled_only)
    )


async def _found(conn: AsyncConnection, module: str) -> tuple[dict[str, Any], Any]:
    modules = await _modules(conn)
    if module not in modules:
        raise Problem(404, "not_found", f"There's no installed module {module!r}.")
    return modules, modules[module]


async def switch_off(engine: AsyncEngine, module: str) -> None:
    async with owner_transaction(engine) as conn:
        modules, row = await _found(conn, module)
        if row.protected:
            raise _refuse(f"{module} is part of the core; it's always on.")
        if needing := _needing(modules, module, enabled_only=True):
            raise _refuse(f"{', '.join(needing)} need {module}; switch them off first.")
        await switch(conn, module, on=False)


async def switch_on(engine: AsyncEngine, module: str) -> None:
    async with owner_transaction(engine) as conn:
        modules, row = await _found(conn, module)
        needs: list[str] = list(row.depends_on or {})
        missing = sorted(o for o in needs if o not in modules or not modules[o].enabled)
        if missing:
            raise _refuse(f"{module} needs {', '.join(missing)}; switch them on first.")
        await switch(conn, module, on=True)


async def remove(engine: AsyncEngine, module: str) -> None:
    """Retires the module: its data stays, hidden and read-only; installing it brings it back."""
    async with owner_transaction(engine) as conn:
        modules, row = await _found(conn, module)
        if row.protected:
            raise _refuse(f"{module} is part of the core; it can't be removed.")
        if row.enabled:
            raise _refuse(f"Switch {module} off before removing it.")
        if needing := _needing(modules, module, enabled_only=False):
            raise _refuse(f"{', '.join(needing)} need {module}; remove them first.")
        await write_declarations(conn, module, [])
        await write_roles(conn, await load_catalog(conn), module, [])
        await write_docs(conn, module, None)
        await conn.execute(
            text("UPDATE sys.module SET deleted = now() WHERE name = :id"), {"id": module}
        )
        await settle_updates(conn, module, None)
