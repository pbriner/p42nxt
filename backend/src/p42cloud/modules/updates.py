"""Learning about updates: read the registry's signed index, and record the updates that fit.

The installation only downloads the public index and its signature; it sends nothing about
itself. An index not signed by a trusted key is refused. The result is records
(`sys.module_update`), security updates first; a daily job keeps them current.
"""

from urllib.parse import urlsplit

import httpx
from packaging.version import Version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.kernel.config import get_settings
from p42cloud.kernel.db import owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.outgoing import safe_client
from p42cloud.modules import releases
from p42cloud.modules.install import states
from p42cloud.services.jobs.models import JobRun


async def fetch_index(
    http: httpx.AsyncClient, registry: str, keys: tuple[str, ...] = releases.TRUSTED_KEYS
) -> dict[str, object]:
    base = registry.rstrip("/")
    try:
        body = await http.get(f"{base}/index.json")
        signature = await http.get(f"{base}/index.json.sig")
        body.raise_for_status()
        signature.raise_for_status()
        return releases.read_index(body.content, signature.text.strip(), keys)
    except (httpx.HTTPError, ValueError) as error:
        raise Problem(
            502, "registry", "The module registry can't be read.", detail=str(error)
        ) from error


async def _write(conn: AsyncConnection, found: list[releases.Update]) -> None:
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
                "installed": update.installed,
                "available": update.available,
                "kind": "security" if update.security else "normal",
                "notes": update.notes or None,
                "fits": update.fits,
                "needs": update.needs,
                "package": update.package[:500] or None,
            },
        )


async def refresh(
    engine: AsyncEngine,
    http: httpx.AsyncClient,
    registry: str | None,
    keys: tuple[str, ...] = releases.TRUSTED_KEYS,
) -> list[releases.Update]:
    """The updates for what's installed, written as records; old ones retired."""
    if registry is None:
        raise Problem(409, "no_registry", "No module registry is set (P42_REGISTRY_URL).")
    index = await fetch_index(http, registry, keys)
    installed = {name: s.version for name, s in (await states(engine)).items() if not s.removed}
    found = releases.updates(index, installed)
    async with owner_transaction(engine) as conn:
        await _write(conn, found)
    return found


async def check_updates(run: JobRun) -> str:
    """The daily check: the registry's index through the safe client, only to its own host."""
    registry = get_settings().registry_url
    if registry is None:
        return "No module registry is set: nothing to check."
    conn = run.records.conn
    installed = {
        r.name: Version(r.version)
        for r in await conn.execute(
            text("SELECT name, version FROM sys.module WHERE deleted IS NULL")
        )
    }
    host = urlsplit(registry).hostname or ""
    async with safe_client(allowed_hosts=(host,)) as http:
        index = await fetch_index(http, registry)
    found = releases.updates(index, installed)
    await _write(conn, found)
    security = sum(1 for u in found if u.security)
    return (
        f"{len(found)} updates, {security} for security." if found else "Everything is up to date."
    )
