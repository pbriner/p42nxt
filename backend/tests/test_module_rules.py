"""Modules import only the SDK from the platform, and never each other (AGENTS.md).

Read from the source, so it holds for every module in modules/, its tests included. Loading
code by name (`importlib`, `__import__`) is refused too: it would get past reading the source.
"""

import ast
from pathlib import Path

import pytest

MODULES = Path(__file__).resolve().parents[2] / "modules"
PLATFORM, SDK = "p42cloud", "p42cloud.sdk"


def _packages() -> list[Path]:
    return sorted(p for p in MODULES.iterdir() if (p / "pyproject.toml").is_file())


def _imports(file: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(file.read_text(), str(file))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ):
            found.append((node.lineno, "__import__"))
        if isinstance(node, ast.Import):
            found += [(node.lineno, alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _wrong(name: str, own: str) -> bool:
    top = name.split(".", maxsplit=1)[0]
    if top in ("importlib", "__import__"):
        return True
    if top == PLATFORM:
        return name != SDK and not name.startswith(f"{SDK}.")
    return top.startswith(f"{PLATFORM}_") and top != own


@pytest.mark.parametrize("package", _packages(), ids=lambda p: p.name)
def test_a_module_imports_only_the_sdk(package: Path) -> None:
    own = f"{PLATFORM}_{package.name}"
    wrong = [
        f"{file.relative_to(package)}:{line} imports {name}"
        for file in sorted(package.rglob("*.py"))
        if ".venv" not in file.parts
        for line, name in _imports(file)
        if _wrong(name, own)
    ]
    assert wrong == []


def test_the_rule_catches_what_it_should() -> None:
    assert _wrong("p42cloud.data.records", "p42cloud_task")
    assert _wrong("p42cloud", "p42cloud_task")
    assert _wrong("p42cloud_cmdb.models", "p42cloud_task")
    assert not _wrong("p42cloud.sdk", "p42cloud_task")
    assert not _wrong("p42cloud.sdk.testing", "p42cloud_task")
    assert not _wrong("p42cloud_task.models", "p42cloud_task")
    assert not _wrong("pytest", "p42cloud_task")
    assert _wrong("importlib", "p42cloud_task")
    assert _wrong("importlib.util", "p42cloud_task")
    assert _wrong("__import__", "p42cloud_task")
