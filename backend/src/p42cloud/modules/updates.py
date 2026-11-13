"""Learning about updates: read the registry's signed index, and record the updates that fit.

The installation only downloads the public index and its signature; it sends nothing about
itself. An index not signed by a trusted key is refused. The result is records
(`sys.module_update`), security updates first; a daily job and notifications use them later.
"""

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.db import owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules import releases
from p42cloud.modules.install import states


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
        await conn.execute(
            text("UPDATE sys.module_update SET deleted = now() WHERE deleted IS NULL")
        )
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
    return found
