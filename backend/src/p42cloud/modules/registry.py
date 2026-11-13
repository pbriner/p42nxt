"""Finding the installed modules: each module package names its manifest as an entry point.

[project.entry-points."p42cloud.modules"]
task = "p42cloud_task:module"
"""

from importlib.metadata import entry_points

from p42cloud.kernel.errors import Problem
from p42cloud.modules.manifest import Module

GROUP = "p42cloud.modules"


def discover() -> list[Module]:
    """Every module package installed in this environment, by its manifest."""
    found: list[Module] = []
    for entry in sorted(entry_points(group=GROUP), key=lambda e: e.name):
        module = entry.load()
        if not isinstance(module, Module) or module.id != entry.name:
            raise Problem(
                422,
                "invalid_module",
                f"The package {entry.value!r} doesn't name a module {entry.name!r}.",
            )
        found.append(module)
    return found
