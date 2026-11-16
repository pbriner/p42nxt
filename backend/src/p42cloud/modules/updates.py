"""Learning about updates: read the registry's signed index, and record the updates that fit.

The installation only downloads the public index and its signature; it sends nothing about
itself. An index not signed by a trusted key is refused. The result is records
(`sys.module_update`), security updates first; a daily job keeps them current.
"""

import httpx
from packaging.version import Version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data import installation
from p42cloud.data.settings import value
from p42cloud.kernel.config import get_settings
from p42cloud.kernel.db import owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.outgoing import safe_client
from p42cloud.modules import releases
from p42cloud.modules.packages import download, hosts_for
from p42cloud.services.jobs.models import JobRun
from p42cloud.services.notifications.service import send as notify


async def fetch_index(
    http: httpx.AsyncClient, registry: str, keys: tuple[str, ...] = releases.TRUSTED_KEYS
) -> dict[str, object]:
    base = registry.rstrip("/")
    body = await download(http, f"{base}/index.json")
    signature = await download(http, f"{base}/index.json.sig")
    try:
        return releases.read_index(body, signature.decode().strip(), keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise Problem(
            502, "registry", "The module registry can't be read.", detail=str(error)
        ) from error


async def _write(conn: AsyncConnection, found: list[releases.Update]) -> None:
    """The updates and the modules one could install, as records (those before retired);
    administrators hear of a module offered for the first time."""
    offered_before = set(
        (
            await conn.execute(
                text("SELECT module FROM sys.module_update WHERE kind = 'new' AND deleted IS NULL")
            )
        ).scalars()
    )
    await conn.execute(text("UPDATE sys.module_update SET deleted = now() WHERE deleted IS NULL"))
    for update in found:
        await conn.execute(
            text(
                "INSERT INTO sys.module_update"
                " (module, installed, available, kind, notes, fits, needs, package)"
                " VALUES (:module, :installed, :available, :kind, :notes, :fits, :needs,"
                " :package)"
            ),
            {
                "module": update.module,
                "installed": update.installed or None,
                "available": update.available,
                "kind": "new"
                if not update.installed
                else "security"
                if update.security
                else "normal",
                "notes": update.notes or None,
                "fits": update.fits,
                "needs": update.needs,
                "package": update.package[:500] or None,
            },
        )
    for update in found:
        if not update.installed and update.module not in offered_before:
            await notify(
                conn,
                "sys.new_module",
                f"A module to install: {update.module} {update.available}",
                roles=("sys.admin",),
                message=update.notes[:500] or None,
                link="/list/sys.module_update",
            )


async def _offers(conn: AsyncConnection, index: dict[str, object]) -> list[releases.Update]:
    """The updates of what's installed, then the modules one could install."""
    rows = list(
        await conn.execute(
            text("SELECT name, version, depends_on, deleted IS NOT NULL AS removed FROM sys.module")
        )
    )
    installed = {r.name: Version(r.version) for r in rows if not r.removed}
    requires = {r.name: dict(r.depends_on or {}) for r in rows if not r.removed}
    removed = frozenset(r.name for r in rows if r.removed)
    return [
        *releases.updates(index, installed, requires),
        *releases.installable(index, installed, requires, removed),
    ]


async def refresh(
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    registry: str | None,
    keys: tuple[str, ...] = releases.TRUSTED_KEYS,
) -> list[releases.Update]:
    """The updates for what's installed, written as records; old ones retired."""
    if registry is None:
        raise Problem(
            409, "no_registry", "No release address is set (Platform, Settings, Release address)."
        )
    index = await fetch_index(http, registry, keys)
    async with engine.connect() as conn:
        found = await _offers(conn, index)
    async with owner_transaction(engine) as conn:
        await _write(conn, found)
    return found


async def check_updates(run: JobRun) -> str:
    """The daily check: the registry's index through the safe client, only to its own host."""
    conn = run.records.conn
    found_address = await value(conn, installation.RELEASE_ADDRESS.name)
    registry = str(found_address or "") or get_settings().registry_url
    if registry is None:
        return "No release address is set (Settings): nothing to check."
    async with safe_client(allowed_hosts=hosts_for(registry)) as http:
        index = await fetch_index(http, registry)
    found = await _offers(conn, index)
    await _write(conn, found)
    new = sum(1 for u in found if not u.installed)
    security = sum(1 for u in found if u.security)
    if not found:
        return "Everything is up to date."
    return f"{len(found) - new} updates, {security} for security; {new} modules to install."
