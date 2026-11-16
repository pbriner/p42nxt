"""Releases: signatures, changelogs, the registry index, and which updates fit an installation.

A release is a module's package (a wheel), signed with the release key (Ed25519, detached). The
registry index lists every module's releases (version, what it depends on, normal or security,
release notes, the package's address, hash and signature) and is signed itself. Installations
trust only the public keys shipped with the platform: with none, nothing verifies.
"""

import base64
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

# The publishers this platform trusts, each with the public halves of its release keys (base64).
# A package is believed only when its own publisher's key signed it. Add a key before releasing
# with it; remove one only when nothing signed with it is in use any more.
PUBLISHERS: dict[str, tuple[str, ...]] = {
    "p42": ("5LJak2r9/Jji0BRfMTSZdkmtwsjwLD3R2GPfcBb2oQs=",),  # the maintainer's, since 2026-11-15
}
# Any trusted publisher's key: what a registry's index may be signed with.
TRUSTED_KEYS: tuple[str, ...] = tuple(k for keys in PUBLISHERS.values() for k in keys)

INDEX_FORMAT = 1
_HEADING = re.compile(r"^## +(?P<version>\S+)(?P<security> +\(security\))? *$")


# ---- signatures ----


def new_key() -> tuple[str, str]:
    """A new release key: (private, public), both base64. The private one is a CI secret."""
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    raw = private.private_bytes_raw()
    return base64.b64encode(raw).decode(), base64.b64encode(public).decode()


def sign(private: str, data: bytes) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private))
    return base64.b64encode(key.sign(data)).decode()


def verify(data: bytes, signature: str, keys: tuple[str, ...] = TRUSTED_KEYS) -> bool:
    """Whether one of the trusted keys signed the data; never with no key at all."""
    try:
        raw = base64.b64decode(signature, validate=True)
    except ValueError:
        return False
    for key in keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(raw, data)
        except InvalidSignature, ValueError:
            continue
        return True
    return False


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- changelogs ----


@dataclass(frozen=True)
class Entry:
    version: str
    security: bool
    notes: str


def changelog(text: str) -> dict[str, Entry]:
    """The entries of a CHANGELOG.md: `## 1.4.0` or `## 1.4.1 (security)`, then the notes."""
    entries: dict[str, Entry] = {}
    current: re.Match[str] | None = None
    lines: list[str] = []

    def close() -> None:
        if current is not None:
            entries[current["version"]] = Entry(
                current["version"], bool(current["security"]), "\n".join(lines).strip()
            )

    for line in text.splitlines():
        found = _HEADING.match(line)
        if found:
            close()
            current, lines = found, []
        elif current is not None:
            lines.append(line)
    close()
    return entries


# ---- the index ----


def empty_index() -> dict[str, Any]:
    return {"format": INDEX_FORMAT, "modules": {}}


def wheel_version(name: str) -> str | None:
    """The version in a wheel's file name (`p42cloud_dca-0.7.2-py3-none-any.whl`)."""
    parts = name.split("-")
    return parts[1] if name.endswith(".whl") and len(parts) >= 5 else None  # noqa: PLR2004


def drop_mismatched(index: dict[str, Any]) -> dict[str, Any]:
    """The index without entries whose package is another version than they say: a release
    published under a wrong version must not be installed as that version."""
    modules = cast(dict[str, list[dict[str, Any]]], index.get("modules") or {})
    for module, entries in modules.items():
        modules[module] = [
            e
            for e in entries
            if (packaged := wheel_version(str(e.get("package", "")).rsplit("/", 1)[-1])) is None
            or packaged == e["version"]
        ]
    return index


def add_release(  # noqa: PLR0913 - one release, described in full
    index: dict[str, Any],
    module: str,
    version: str,
    *,
    depends_on: dict[str, str],
    entry: Entry,
    package: str,
    digest: str,
    signature: str,
    migrations: tuple[str, ...] = (),
) -> dict[str, Any]:
    """The index with this release added (a release already there is replaced). Refused when
    the release drops a data migration an earlier one had: an installation skipping that
    release would never move its data."""
    releases = cast(dict[str, list[dict[str, Any]]], index.setdefault("modules", {}))
    found = [r for r in releases.get(module, []) if r["version"] != version]
    if gone := lost_migrations(found, version, migrations):
        raise ValueError(
            f"{module} {version} drops data migrations earlier releases had: {', '.join(gone)}."
            " Keep every migration in the module for good."
        )
    found.append(
        {
            "version": version,
            "depends_on": depends_on,
            "migrations": sorted(migrations, key=Version),
            "kind": "security" if entry.security else "normal",
            "notes": entry.notes,
            "package": package,
            "sha256": digest,
            "signature": signature,
        }
    )
    releases[module] = sorted(found, key=lambda r: Version(r["version"]))
    return index


def lost_migrations(
    earlier: list[dict[str, Any]], version: str, migrations: tuple[str, ...]
) -> list[str]:
    """Migrations that releases before `version` had and this one hasn't."""
    had: set[str] = set()
    for release in earlier:
        if Version(release["version"]) < Version(version):
            had.update(cast(list[str], release.get("migrations", [])))
    return sorted(had - set(migrations), key=Version)


def read_index(data: bytes, signature: str, keys: tuple[str, ...] = TRUSTED_KEYS) -> dict[str, Any]:
    """The index, if a trusted key signed it; otherwise nothing is believed."""
    if not verify(data, signature, keys):
        raise ValueError("the registry index isn't signed by a trusted key")
    index = cast(dict[str, Any], json.loads(data))
    if index.get("format") != INDEX_FORMAT:
        raise ValueError(f"unknown index format {index.get('format')!r}")
    return index


# ---- which updates fit ----


@dataclass(frozen=True)
class Update:
    module: str
    installed: str
    available: str
    security: bool
    notes: str
    package: str
    needs: str | None  # what must be updated first; None: it fits now

    @property
    def fits(self) -> bool:
        return self.needs is None


def _blockers(
    index_entry: dict[str, Any],
    module: str,
    installed: dict[str, Version],
    requires: Mapping[str, Mapping[str, str]],
) -> list[str]:
    """What stands in the way of this release: what it needs that isn't installed, and
    installed modules that don't accept its version (an old module on a new core)."""
    found = [
        f"{other} {wanted}"
        for other, wanted in cast(dict[str, str], index_entry.get("depends_on", {})).items()
        if other not in installed or installed[other] not in SpecifierSet(wanted)
    ]
    version = Version(index_entry["version"])
    found += [
        f"a newer {other} ({other} accepts {module} {needs[module]})"
        for other, needs in sorted(requires.items())
        if other != module and module in needs and version not in SpecifierSet(needs[module])
    ]
    return found


def _notes(passed: list[dict[str, Any]]) -> str:
    """The notes of every release passed on the way, newest first, each under its version."""
    if len(passed) == 1:
        return str(passed[0].get("notes", ""))
    return "\n\n".join(
        f"## {r['version']}\n\n{r.get('notes', '')}".rstrip() for r in reversed(passed)
    )


def installable(
    index: dict[str, Any],
    installed: dict[str, Version],
    requires: Mapping[str, Mapping[str, str]] | None = None,
    removed: frozenset[str] = frozenset(),
) -> list[Update]:
    """The modules of the index not installed (nor removed: those come back by name): for each,
    its newest release that fits, else its newest, saying what it needs. The core isn't one."""
    requires = requires or {}
    found: list[Update] = []
    modules = cast(dict[str, list[dict[str, Any]]], index.get("modules", {}))
    for module, releases in sorted(modules.items()):
        if module in installed or module in removed or module == "sys" or not releases:
            continue
        ordered: list[dict[str, Any]] = []
        for release in releases:
            try:
                Version(release["version"])
            except InvalidVersion:
                continue
            ordered.append(release)
        if not ordered:
            continue
        ordered.sort(key=lambda r: Version(r["version"]))
        blocked = {r["version"]: _blockers(r, module, installed, requires) for r in ordered}
        fitting = [r for r in ordered if not blocked[r["version"]]]
        chosen = fitting[-1] if fitting else ordered[-1]
        missing = blocked[chosen["version"]]
        found.append(
            Update(
                module=module,
                installed="",
                available=chosen["version"],
                security=False,
                notes=str(chosen.get("notes", "")),
                package=str(chosen.get("package", "")),
                needs=f"needs {', '.join(missing)}" if missing else None,
            )
        )
    return found


def updates(
    index: dict[str, Any],
    installed: dict[str, Version],
    requires: Mapping[str, Mapping[str, str]] | None = None,
) -> list[Update]:
    """For every installed module, the newest release that fits what's installed (else the
    newest, saying what it needs), with the notes of every release on the way.

    `requires` is what the installed modules depend on: a release an installed module doesn't
    accept (a core it isn't made for) doesn't fit until that module is updated. Security
    updates first.
    """
    requires = requires or {}
    found: list[Update] = []
    modules = cast(dict[str, list[dict[str, Any]]], index.get("modules", {}))
    for module, current in sorted(installed.items()):
        newer: list[dict[str, Any]] = []
        for release in modules.get(module, []):
            try:
                if Version(release["version"]) > current:
                    newer.append(release)
            except InvalidVersion:
                continue
        if not newer:
            continue
        newer.sort(key=lambda r: Version(r["version"]))
        blocked = {r["version"]: _blockers(r, module, installed, requires) for r in newer}
        fitting = [r for r in newer if not blocked[r["version"]]]
        chosen = fitting[-1] if fitting else newer[-1]
        passed = [r for r in newer if Version(r["version"]) <= Version(chosen["version"])]
        missing = blocked[chosen["version"]]
        found.append(
            Update(
                module=module,
                installed=str(current),
                available=chosen["version"],
                security=any(r.get("kind") == "security" for r in passed),
                notes=_notes(passed),
                package=str(chosen.get("package", "")),
                needs=f"needs {', '.join(missing)}" if missing else None,
            )
        )
    return sorted(found, key=lambda u: (not u.security, u.module))
