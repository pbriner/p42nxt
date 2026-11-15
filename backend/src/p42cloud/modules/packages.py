"""Module packages in an installation's modules folder: fetched, checked, unpacked.

A package is believed only if a trusted key signed it: its release in the signed registry index
names its SHA-256 and its signature, and both must match the bytes. It's unpacked into its own
folder (`<modules folder>/<module>/`), replacing the one before only once it's complete (and
put back if installing then fails), and the processes find it there when they start
(`use_folder`). A package holds only its own module, and needs nothing the image doesn't have:
nothing else is installed with it.
"""

import importlib
import importlib.metadata
import io
import shutil
import sys
import uuid
import zipfile
from dataclasses import dataclass
from email.parser import HeaderParser
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from p42cloud.kernel.errors import Problem
from p42cloud.modules import releases

GROUP = "p42cloud.modules"
MAX_FILES = 5000
MAX_BYTES = 50 * 1024 * 1024  # unpacked, and downloaded
MAX_REDIRECTS = 5
FILE_TYPE, SYMLINK = 0o170000, 0o120000  # a zip member's Unix mode bits
# GitHub serves a release's files from its own download hosts, after a redirect.
GITHUB = ("github.com", "*.githubusercontent.com")


def use_folder(folder: Path | None) -> None:
    """Makes the folder's modules importable (and found as entry points) in this process."""
    if folder is None or not folder.is_dir():
        return
    for child in sorted(folder.iterdir()):
        if child.is_dir() and not child.name.startswith(".") and str(child) not in sys.path:
            sys.path.insert(0, str(child))
    importlib.invalidate_caches()


def choose(index: dict[str, Any], module: str, version: str | None) -> dict[str, Any]:
    """The release to install: the one asked for, else the newest."""
    found = cast(dict[str, list[dict[str, Any]]], index.get("modules", {})).get(module, [])
    if version is not None:
        found = [r for r in found if r.get("version") == version]
    if not found:
        wanted = f"{module} {version}" if version else module
        raise Problem(404, "no_release", f"The registry has no release of {wanted}.")
    return max(found, key=lambda r: Version(str(r["version"])))


def needs(release: dict[str, Any], installed: dict[str, Version]) -> list[str]:
    """What must be installed or updated first for the release to fit."""
    missing: list[str] = []
    for other, wanted in cast(dict[str, str], release.get("depends_on", {})).items():
        try:
            fits = other in installed and installed[other] in SpecifierSet(wanted)
        except InvalidSpecifier:
            fits = False
        if not fits:
            missing.append(f"{other} {wanted}")
    return missing


def hosts_for(registry: str) -> tuple[str, ...]:
    """The hosts a registry's files may come from: its own, and GitHub's download hosts."""
    host = (urlsplit(registry).hostname or "").lower()
    return (host, *GITHUB[1:]) if host == "github.com" else (host,)


async def download(http: httpx.AsyncClient, url: str) -> bytes:
    """The file at the address, following a few redirects (the client checks every hop)."""
    try:
        for _ in range(MAX_REDIRECTS + 1):
            async with http.stream("GET", url) as answer:
                if answer.is_redirect:
                    url = str(answer.url.join(answer.headers.get("location", "")))
                    continue
                answer.raise_for_status()
                data = bytearray()
                async for chunk in answer.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise Problem(422, "package_too_big", "The file is too big.")
                return bytes(data)
    except httpx.HTTPError as error:
        raise Problem(
            502, "registry", "The module registry can't be read.", detail=str(error)
        ) from error
    raise Problem(502, "registry", "The module registry redirects too often.")


def check(release: dict[str, Any], data: bytes, keys: tuple[str, ...]) -> None:
    """The bytes are the release's: its digest, and a trusted key's signature."""
    if releases.sha256(data) != release.get("sha256"):
        raise Problem(422, "wrong_package", "The package isn't the one the registry lists.")
    if not releases.verify(data, str(release.get("signature", "")), keys):
        raise Problem(422, "unsigned_package", "No trusted key signed the package.")


def _members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The archive's files, refused if any would land outside the folder or it's too big."""
    members = archive.infolist()
    if len(members) > MAX_FILES or sum(m.file_size for m in members) > MAX_BYTES:
        raise Problem(422, "package_too_big", "The package is too big.")
    for member in members:
        path = PurePosixPath(member.filename)
        unix_mode = member.external_attr >> 16
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in member.filename
            or (unix_mode & FILE_TYPE) == SYMLINK
        ):
            raise Problem(
                422, "bad_package", f"The package holds an unsafe path: {member.filename}"
            )
    return members


def _info(archive: zipfile.ZipFile) -> str:
    """The package's one `.dist-info` folder."""
    found = {
        n.split("/", 1)[0] for n in archive.namelist() if n.split("/", 1)[0].endswith(".dist-info")
    }
    if len(found) != 1:
        raise Problem(422, "bad_package", "The package isn't a module package.")
    return found.pop()


def _names_module(archive: zipfile.ZipFile, info: str, module: str) -> str:
    """The package declares this module as its entry point, and only it; its Python package."""
    try:
        text = archive.read(f"{info}/entry_points.txt").decode()
    except KeyError as error:
        raise Problem(422, "bad_package", "The package isn't a module package.") from error
    section, named = "", list[tuple[str, str]]()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line.strip("[]")
        elif section == GROUP and "=" in line:
            name, target = (part.strip() for part in line.split("=", 1))
            named.append((name, target.split(":", 1)[0].split(".", 1)[0]))
    if [name for name, _ in named] != [module]:
        raise Problem(422, "bad_package", f"The package isn't the module {module}.")
    return named[0][1]


def _only_its_own(archive: zipfile.ZipFile, info: str, package: str) -> None:
    """Nothing beside the module's own package: it mustn't stand in for the platform's code."""
    for name in archive.namelist():
        top = name.split("/", 1)[0]
        if top not in (info, package) or "/" not in name:
            raise Problem(422, "bad_package", f"The package holds more than its module: {name}")


def _needs_nothing_new(archive: zipfile.ZipFile, info: str) -> None:
    """Every library the package needs is already here, in a version it accepts."""
    metadata = HeaderParser().parsestr(archive.read(f"{info}/METADATA").decode())
    for line in metadata.get_all("Requires-Dist") or []:
        try:
            wanted = Requirement(str(line))
        except InvalidRequirement as error:
            raise Problem(422, "bad_package", f"The package needs {line!r}.") from error
        if wanted.marker is not None and not wanted.marker.evaluate({"extra": ""}):
            continue
        try:
            here = importlib.metadata.version(wanted.name)
        except importlib.metadata.PackageNotFoundError:
            here = None
        if here is None or not wanted.specifier.contains(here, prereleases=True):
            raise Problem(
                422,
                "needs_libraries",
                f"The package needs {wanted}, which this installation hasn't.",
            )


@dataclass
class Unpacked:
    """A package now in its folder; the one before is kept until the install is done."""

    target: Path
    before: Path | None
    package: str  # its Python package

    def forget(self) -> None:
        """This process imports the package afresh: the new version, not one loaded before."""
        for name in [
            n for n in sys.modules if n == self.package or n.startswith(f"{self.package}.")
        ]:
            del sys.modules[name]
        importlib.invalidate_caches()

    def done(self) -> None:
        if self.before is not None:
            shutil.rmtree(self.before, ignore_errors=True)

    def undo(self) -> None:
        shutil.rmtree(self.target, ignore_errors=True)
        if self.before is not None:
            self.before.rename(self.target)


def unpack(folder: Path, module: str, data: bytes) -> Unpacked:
    """Unpacks the package into `<folder>/<module>`, replacing what was there once complete."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise Problem(422, "bad_package", "The package isn't a wheel.") from error
    with archive:
        members = _members(archive)
        info = _info(archive)
        package = _names_module(archive, info, module)
        _only_its_own(archive, info, package)
        _needs_nothing_new(archive, info)
        folder.mkdir(parents=True, exist_ok=True)
        staging = folder / f".new-{module}-{uuid.uuid4().hex}"
        try:
            for member in members:
                archive.extract(member, staging)
        except OSError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    target = folder / module
    before = None
    if target.exists():
        before = folder / f".old-{module}-{uuid.uuid4().hex}"
        target.rename(before)
    staging.rename(target)
    return Unpacked(target, before, package)


def version_of(release: dict[str, Any]) -> Version:
    try:
        return Version(str(release["version"]))
    except (InvalidVersion, KeyError) as error:
        raise Problem(
            422, "bad_index", "The registry lists a release without a version."
        ) from error
