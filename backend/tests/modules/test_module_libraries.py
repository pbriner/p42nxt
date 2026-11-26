"""A module brings no libraries of its own (an installation unpacks its package and refuses one
needing a library it hasn't): whatever a module needs, the core already depends on."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[3]


def _needs(pyproject: Path) -> set[str]:
    project = tomllib.loads(pyproject.read_text())["project"]
    return {Requirement(d).name.lower() for d in project.get("dependencies", [])}


def test_every_library_a_module_needs_is_in_the_core() -> None:
    core = _needs(ROOT / "backend" / "pyproject.toml") | {"p42cloud"}
    missing = {
        path.parent.name: sorted(_needs(path) - core)
        for path in sorted((ROOT / "modules").glob("*/pyproject.toml"))
        if _needs(path) - core
    }
    assert missing == {}
