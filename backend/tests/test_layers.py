"""Every package in p42 belongs to a layer, so none can escape the import rules."""

import importlib
import pkgutil
import tomllib
from pathlib import Path

import p42

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"


def declared_layers() -> set[str]:
    config = tomllib.loads(PYPROJECT.read_text())
    contract = next(c for c in config["tool"]["importlinter"]["contracts"] if c["type"] == "layers")
    return {name.strip() for layer in contract["layers"] for name in layer.split("|")}


def test_every_package_is_in_a_layer() -> None:
    packages = {f"p42.{info.name}" for info in pkgutil.iter_modules(p42.__path__) if info.ispkg}
    assert packages == declared_layers()


def test_layers_import() -> None:
    for name in declared_layers():
        assert importlib.import_module(name).__doc__
