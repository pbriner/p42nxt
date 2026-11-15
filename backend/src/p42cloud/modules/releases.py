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
from dataclasses import dataclass
from typing import Any, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

# The public halves of the release keys this platform trusts (base64). Add a new key here before
# releasing with it; remove one only when nothing signed with it is in use any more.
TRUSTED_KEYS: tuple[str, ...] = (
    "5LJak2r9/Jji0BRfMTSZdkmtwsjwLD3R2GPfcBb2oQs=",  # the maintainer's, since 2026-11-15
)

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
) -> dict[str, Any]:
    """The index with this release added (a release already there is replaced)."""
    releases = cast(dict[str, list[dict[str, Any]]], index.setdefault("modules", {}))
    found = [r for r in releases.get(module, []) if r["version"] != version]
    found.append(
        {
            "version": version,
            "depends_on": depends_on,
            "kind": "security" if entry.security else "normal",
            "notes": entry.notes,
            "package": package,
            "sha256": digest,
            "signature": signature,
        }
    )
    releases[module] = sorted(found, key=lambda r: Version(r["version"]))
    return index


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


def updates(index: dict[str, Any], installed: dict[str, Version]) -> list[Update]:
    """For every installed module, its newest release, and whether it fits what's installed.

    Security updates first. A release needing a newer core or another module says so.
    """
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
        latest = max(newer, key=lambda r: Version(r["version"]))
        missing = [
            f"{other} {wanted}"
            for other, wanted in cast(dict[str, str], latest.get("depends_on", {})).items()
            if other not in installed or installed[other] not in SpecifierSet(wanted)
        ]
        found.append(
            Update(
                module=module,
                installed=str(current),
                available=latest["version"],
                security=any(r.get("kind") == "security" for r in newer),
                notes=str(latest.get("notes", "")),
                package=str(latest.get("package", "")),
                needs=f"needs {', '.join(missing)}" if missing else None,
            )
        )
    return sorted(found, key=lambda u: (not u.security, u.module))
