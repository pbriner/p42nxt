"""Release tooling for CI: `python -m p42cloud.modules.release_tool <command>`.

- `check --base <ref>`: every package (the core in backend/, each module in modules/<id>/) whose
  shipped files changed since `ref` must raise its version and have a CHANGELOG.md entry for it.
  Tests and the README aren't shipped.
- `keygen`: a new release key; the private half goes into the CI secret P42_RELEASE_KEY, the
  public half into TRUSTED_KEYS (releases.py).
- `publish`: signs a module's built package and adds it to the signed registry index.
- `notes`: a version's release notes, from a CHANGELOG.md.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

from p42cloud.modules import releases
from p42cloud.modules.registry import discover

ROOT = Path(__file__).resolve().parents[4]
NOT_SHIPPED = ("tests/", "README.md")


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


def check(base: str) -> list[str]:
    """What's missing for the changed packages to be released; empty if nothing."""
    problems: list[str] = []
    for package in _packages():
        where = package.relative_to(ROOT).as_posix()
        changed = [
            f
            for f in _git("diff", "--name-only", f"{base}...HEAD", "--", where).splitlines()
            if not f.removeprefix(f"{where}/").startswith(NOT_SHIPPED)
        ]
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
    body = json.dumps(index, indent=2, sort_keys=True).encode()
    index_path.write_bytes(body)
    index_path.with_name(index_path.name + ".sig").write_text(releases.sign(private, body))


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
    args = parser.parse_args(argv)
    if args.command == "check":
        problems = check(args.base)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    if args.command == "notes":
        entry = releases.changelog(args.changelog.read_text()).get(args.version)
        if entry is None:
            print(f"No entry for {args.version}.", file=sys.stderr)
            return 1
        print(entry.notes)
        return 0
    if args.command == "keygen":
        private, public = releases.new_key()
        print(f"Private (the CI secret P42_RELEASE_KEY, nowhere else):\n  {private}")
        print(f"Public (add to TRUSTED_KEYS in releases.py):\n  {public}")
        return 0
    publish(args.module, args.wheel, args.index, args.url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
