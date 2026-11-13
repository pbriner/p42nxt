"""The field types: one file each, found here by name.

A type is a file in this package with a `TYPE`. New types come only with a core release, so the
set is fixed for a given version of the code.
"""

import importlib
import pkgutil
from types import ModuleType

from p42cloud.data.types.base import Field, FieldType, Invalid


def _load() -> dict[str, FieldType]:
    found: dict[str, FieldType] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name == "base":
            continue
        unit: ModuleType = importlib.import_module(f"{__name__}.{info.name}")
        field_type: FieldType = unit.TYPE
        found[field_type.name] = field_type
    return found


FIELD_TYPES: dict[str, FieldType] = _load()

__all__ = ["FIELD_TYPES", "Field", "FieldType", "Invalid"]
