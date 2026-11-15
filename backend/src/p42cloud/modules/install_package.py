"""Installing a module from a signed package: from the registry, or from a file next to its
signature. The package is checked, unpacked into the modules folder, and the module installed or
upgraded; if that fails, the package before is put back. Each install is a security event.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from packaging.version import Version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.db import owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules import packages, releases
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules, states
from p42cloud.modules.registry import discover
from p42cloud.modules.updates import fetch_index


@dataclass(frozen=True)
class Source:
    """Where the package comes from: the registry (a version, or the newest), or a file."""

    registry: str | None = None
    version: str | None = None
    file: Path | None = None


async def _from_registry(
    http: httpx.AsyncClient,
    source: Source,
    module: str,
    installed: dict[str, Version],
    keys: tuple[str, ...],
) -> tuple[bytes, str]:
    if source.registry is None:
        raise Problem(
            409, "no_registry", "No release address is set (Platform, Settings, Release address)."
        )
    index = await fetch_index(http, source.registry, keys)
    release: dict[str, Any] = packages.choose(index, module, source.version)
    missing = packages.needs(release, installed)
    if missing:
        raise Problem(409, "needs_first", f"Install or update first: {', '.join(missing)}.")
    data = await packages.download(http, str(release.get("package", "")))
    packages.check(release, data, keys)
    return data, str(packages.version_of(release))


def _from_file(file: Path, keys: tuple[str, ...]) -> bytes:
    signature = file.with_name(f"{file.name}.sig")
    if not file.is_file() or not signature.is_file():
        raise Problem(404, "no_package", f"{file} and its signature {signature.name} are needed.")
    data = file.read_bytes()
    if len(data) > packages.MAX_BYTES:
        raise Problem(422, "package_too_big", "The package is too big.")
    if not releases.verify(data, signature.read_text().strip(), keys):
        raise Problem(422, "unsigned_package", "No trusted key signed the package.")
    return data


async def install_package(  # noqa: PLR0913 - what, where to, from where, and the means
    engine: AsyncEngine,
    module: str,
    folder: Path | None,
    source: Source,
    http: httpx.AsyncClient,
    *,
    keys: tuple[str, ...] = releases.TRUSTED_KEYS,
) -> list[str]:
    """Installs or upgrades the module from its package; what changed."""
    if folder is None:
        raise Problem(409, "no_modules_folder", "No modules folder is set (P42_MODULES_DIR).")
    known = await states(engine)
    installed = {name: s.version for name, s in known.items() if not s.removed}
    if source.file is not None:
        data, version = _from_file(source.file, keys), None
    else:
        data, version = await _from_registry(http, source, module, installed, keys)
    unpacked = packages.unpack(folder, module, data)
    try:
        unpacked.forget()
        found = {m.id: m for m in discover(folder)}
        if module not in found:
            raise Problem(422, "bad_package", f"The package doesn't hold the module {module}.")
        if version is not None and found[module].version != version:
            raise Problem(422, "bad_package", "The package's version isn't the registry's.")
        # Only this module joins: others found here but not installed stay out.
        chosen = [m for m in found.values() if m.id == module or m.id in installed]
        done = await install_modules(engine, [CORE, *chosen], bring_back=frozenset({module}))
        details = {
            "module": module,
            "version": found[module].version,
            "sha256": releases.sha256(data),
            "source": "file" if source.file is not None else source.registry,
        }
        async with owner_transaction(engine) as conn:  # the owner's own function: no role switch
            await conn.execute(
                text(
                    "SELECT sys.p42_security_event('module_package_installed', CAST(:d AS jsonb))"
                ),
                {"d": json.dumps(details)},
            )
    except BaseException:
        unpacked.undo()
        unpacked.forget()
        raise
    unpacked.done()
    return done
