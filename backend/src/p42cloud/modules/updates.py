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


async def _installed(
    conn: AsyncConnection,
) -> tuple[dict[str, Version], dict[str, dict[str, str]]]:
    """The installed modules' versions, and what each depends on."""
    rows = list(
        await conn.execute(
            text("SELECT name, version, depends_on FROM sys.module WHERE deleted IS NULL")
        )
    )
    return (
        {r.name: Version(r.version) for r in rows},
        {r.name: dict(r.depends_on or {}) for r in rows},
    )


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
        installed, requires = await _installed(conn)
    found = releases.updates(index, installed, requires)
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
    installed, requires = await _installed(conn)
    async with safe_client(allowed_hosts=hosts_for(registry)) as http:
        index = await fetch_index(http, registry)
    found = releases.updates(index, installed, requires)
    await _write(conn, found)
    security = sum(1 for u in found if u.security)
    return (
        f"{len(found)} updates, {security} for security." if found else "Everything is up to date."
    )
