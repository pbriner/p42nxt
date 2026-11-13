"""The installed modules, and their documentation (the wiki shows these pages)."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.json import Json
from p42cloud.data.types.markdown import Markdown
from p42cloud.data.types.string import String

MODULE = Table(
    "module",
    "Module",
    {
        "name": String("ID", max_length=16, required=True, unique=True),
        "label": String("Name", max_length=80, required=True),
        "version": String("Version", max_length=40, required=True),
        "depends_on": Json("Depends on"),
        "description": String("Description", max_length=255),
        # Switched off: its tables and roles count for nobody; its data stays.
        "enabled": Boolean("On", required=True, default=True),
        # The core and what it depends on: never switched off or removed.
        "protected": Boolean("Part of the core", required=True, default=False),
    },
    label_plural="Modules",
)

DOC_PAGE = Table(
    "doc_page",
    "Documentation page",
    {
        "module": String("Module", max_length=16, required=True),
        "path": String("Path", max_length=255, required=True),
        "title": String("Title", max_length=255, required=True),
        "body": Markdown("Text", required=True),
    },
    label_plural="Documentation pages",
    unique_together=(("module", "path"),),
)

MODULE_UPDATE = Table(
    "module_update",
    "Available update",
    {
        "module": String("Module", max_length=16, required=True),
        "installed": String("Installed", max_length=40, required=True),
        "available": String("Available", max_length=40, required=True),
        "kind": Choice(
            "Kind",
            {"normal": "Update", "security": "Security update"},
            required=True,
            default="normal",
        ),
        "notes": Markdown("Release notes"),
        # It fits what's installed now; otherwise `needs` says what to update first.
        "fits": Boolean("Fits", required=True, default=False),
        "needs": String("Needs first", max_length=255),
        "package": String("Package", max_length=500),
    },
    label_plural="Available updates",
)

TABLES = [MODULE, DOC_PAGE, MODULE_UPDATE]
