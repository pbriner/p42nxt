"""The installed modules, and their documentation (the wiki shows these pages)."""

from p42cloud.data.declarations import Table
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

TABLES = [MODULE, DOC_PAGE]
