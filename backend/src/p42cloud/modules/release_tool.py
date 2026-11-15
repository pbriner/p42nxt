"""Release tooling for CI: `python -m p42cloud.modules.release_tool <command>`.

- `check --base <ref>`: every package (the core in backend/, each module in modules/<id>/) whose
  shipped files changed since `ref` must raise its version and have a CHANGELOG.md entry for it.
  Tests and the README aren't shipped.
- `keygen`: a new release key; the private half goes into the CI secret P42_RELEASE_KEY, the
  public half into TRUSTED_KEYS (releases.py).
- `publish`: signs a module's built package and adds it to the signed registry index.
- `publish-image`: signs the core's image (by its digest) and adds it to the index.
- `sign --file <package>`: signs a package by hand (writes <package>.sig), to install it from
  the file; asks for the private key unless P42_RELEASE_KEY has it.
- `notes`: a version's release notes, from a CHANGELOG.md.
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from p42cloud.modules import releases
from p42cloud.modules.core_release import IMAGE
from p42cloud.modules.manifest import CORE
from p42cloud.modules.registry import discover

ROOT = Path(__file__).resolve().parents[4]
NOT_SHIPPED = ("tests/", "README.md")
# The browser app ships in the core's image: changing it releases the core. Its browser tests
# and their setup don't ship.
FRONTEND = "frontend"
FRONTEND_NOT_SHIPPED = ("e2e/", "README.md", "playwright.config.ts")


def _git(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is needed")
    found = subprocess.run([git, *args], cwd=ROOT, check=True, capture_output=True, text=True)  # noqa: S603
    return found.stdout


def _packages() -> list[Path]:
    modules = sorted(p for p in (ROOT / "modules").iterdir() if (p / "pyproject.toml").is_file())
    return [ROOT / "backend", *modules]


def _version(text: str) -> Version:
    return Version(tomllib.loads(text)["project"]["version"])


def _changed(base: str, where: str, not_shipped: tuple[str, ...]) -> list[str]:
    return [
        f
        for f in _git("diff", "--name-only", f"{base}...HEAD", "--", where).splitlines()
        if not f.removeprefix(f"{where}/").startswith(not_shipped)
    ]


def check(base: str) -> list[str]:
    """What's missing for the changed packages to be released; empty if nothing."""
    problems: list[str] = []
    for package in _packages():
        where = package.relative_to(ROOT).as_posix()
        changed = _changed(base, where, NOT_SHIPPED)
        if where == "backend":
            changed += _changed(base, FRONTEND, FRONTEND_NOT_SHIPPED)
        if not changed:
            continue
        now = _version((package / "pyproject.toml").read_text())
        try:
            before: Version | None = _version(_git("show", f"{base}:{where}/pyproject.toml"))
        except subprocess.CalledProcessError, InvalidVersion:
            before = None  # a new package
        if before is not None and now <= before:
            problems.append(f"{where}: changed, so raise its version (it's {now}).")
        log = package / "CHANGELOG.md"
        if not log.is_file() or str(now) not in releases.changelog(log.read_text()):
            problems.append(f"{where}: add a CHANGELOG.md entry `## {now}`.")
    return problems


def publish(module_id: str, wheel: Path, index_path: Path, url: str) -> None:
    """Signs the package (writes <wheel>.sig) and adds it to the index (and index.json.sig)."""
    private = os.environ["P42_RELEASE_KEY"]
    module = next(m for m in discover() if m.id == module_id)
    log = releases.changelog((ROOT / "modules" / module_id / "CHANGELOG.md").read_text())
    if module.version not in log:
        raise SystemExit(f"No CHANGELOG.md entry for {module_id} {module.version}.")
    data = wheel.read_bytes()
    signature = releases.sign(private, data)
    wheel.with_name(wheel.name + ".sig").write_text(signature)
    index = json.loads(index_path.read_text()) if index_path.is_file() else releases.empty_index()
    index = releases.add_release(
        index,
        module_id,
        module.version,
        depends_on=dict(module.depends_on),
        entry=log[module.version],
        package=url,
        digest=releases.sha256(data),
        signature=signature,
    )
    _write_index(index, index_path, private)


def _write_index(index: dict[str, Any], index_path: Path, private: str) -> None:
    body = json.dumps(index, indent=2, sort_keys=True).encode()
    index_path.write_bytes(body)
    index_path.with_name(index_path.name + ".sig").write_text(releases.sign(private, body))


def publish_image(image: str, index_path: Path) -> None:
    """Adds the core's release (its image by digest, signed) to the index."""
    private = os.environ["P42_RELEASE_KEY"]
    match = IMAGE.match(image)
    if match is None:
        raise SystemExit(f"Name the image by its digest (…@sha256:…), not {image!r}.")
    version = str(_version((ROOT / "backend" / "pyproject.toml").read_text()))
    log = releases.changelog((ROOT / "backend" / "CHANGELOG.md").read_text())
    if version not in log:
        raise SystemExit(f"No CHANGELOG.md entry for the core {version}.")
    index = json.loads(index_path.read_text()) if index_path.is_file() else releases.empty_index()
    index = releases.add_release(
        index,
        CORE,
        version,
        depends_on={},
        entry=log[version],
        package=image,
        digest=match["digest"],
        signature=releases.sign(private, image.encode()),
    )
    _write_index(index, index_path, private)


def sign_file(file: Path) -> Path:
    """Writes <file>.sig with the release key: from P42_RELEASE_KEY, or asked for (not shown)."""
    private = os.environ.get("P42_RELEASE_KEY") or getpass.getpass("Private release key: ")
    try:
        signature = releases.sign(private.strip(), file.read_bytes())
    except ValueError as error:
        raise SystemExit("That isn't a release key's private half.") from error
    if not releases.verify(file.read_bytes(), signature):
        raise SystemExit("Signed, but by a key the platform doesn't trust (TRUSTED_KEYS).")
    target = file.with_name(f"{file.name}.sig")
    target.write_text(signature)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m p42cloud.modules.release_tool")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument("--base", required=True)
    commands.add_parser("keygen")
    notes = commands.add_parser("notes")
    notes.add_argument("--changelog", type=Path, required=True)
    notes.add_argument("--version", required=True)
    published = commands.add_parser("publish")
    published.add_argument("--module", required=True)
    published.add_argument("--wheel", type=Path, required=True)
    published.add_argument("--index", type=Path, required=True)
    published.add_argument("--url", required=True)
    signed = commands.add_parser("sign")
    signed.add_argument("--file", type=Path, required=True)
    image = commands.add_parser("publish-image")
    image.add_argument("--image", required=True)
    image.add_argument("--index", type=Path, required=True)
    args = parser.parse_args(argv)
    run: dict[str, Callable[[], int]] = {
        "check": lambda: _check(args.base),
        "notes": lambda: _notes(args.changelog, args.version),
        "keygen": _keygen,
        "sign": lambda: _done(f"Signed: {sign_file(args.file)}"),
        "publish-image": lambda: _done(publish_image(args.image, args.index)),
        "publish": lambda: _done(publish(args.module, args.wheel, args.index, args.url)),
    }
    return run[args.command]()


def _done(said: str | None = None) -> int:
    if said:
        print(said)
    return 0


def _check(base: str) -> int:
    problems = check(base)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def _notes(changelog: Path, version: str) -> int:
    entry = releases.changelog(changelog.read_text()).get(version)
    if entry is None:
        print(f"No entry for {version}.", file=sys.stderr)
        return 1
    print(entry.notes)
    return 0


def _keygen() -> int:
    private, public = releases.new_key()
    print(f"Private (the CI secret P42_RELEASE_KEY, nowhere else):\n  {private}")
    print(f"Public (add to TRUSTED_KEYS in releases.py):\n  {public}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
