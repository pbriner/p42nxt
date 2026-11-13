"""What a module is: its manifest, the one object a module package exports.

    module = Module(
        id="task", name="Tasks", version="1.4.0",
        depends_on={"sys": ">=0.1,<1"},
        tables=(TASK,), roles=(EDITOR, READER),
        hooks=(TableHook("task.task", before=check_due_date),),
        docs=Path(__file__).parent / "docs",
    )

Installing or upgrading writes all of it into records; at runtime the platform reads the records,
not this object. Only the hooks are code that runs.
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.declarations import Table
from p42cloud.data.hooks import TableHook
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.roles import Role

# Called as the owner after the module's tables and roles are written, with the catalog.
type Seed = Callable[[AsyncConnection, Catalog], Awaitable[None]]

MODULE_ID = re.compile(r"^[a-z][a-z0-9]{1,15}$")
CORE = "sys"
RESERVED = frozenset({"u"})  # admins' own tables


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    version: str
    # Other modules it needs, with the versions it works with: {"sys": ">=0.1,<1"}.
    depends_on: Mapping[str, str] = field(default_factory=dict[str, str])
    tables: tuple[Table, ...] = ()
    roles: tuple[Role, ...] = ()
    hooks: tuple[TableHook, ...] = ()
    # A folder of Markdown files: the module's documentation, written into the wiki's pages.
    docs: Path | None = None
    description: str = ""
    # Records the module makes once installed (the core's starting groups); must be safe to run
    # again at every upgrade.
    seed: Seed | None = None

    @property
    def parsed_version(self) -> Version:
        return Version(self.version)

    def problems(self) -> list[FieldError]:
        """What's wrong with the manifest itself, before anything is installed."""
        found: list[FieldError] = []
        if not MODULE_ID.match(self.id) or self.id in RESERVED:
            found.append(
                FieldError("id", "bad_id", "2 to 16 of a-z and 0-9, starting with a letter.")
            )
        try:
            Version(self.version)
        except InvalidVersion:
            found.append(FieldError("version", "bad_version", f"Not a version: {self.version!r}."))
        for other, wanted in self.depends_on.items():
            try:
                SpecifierSet(wanted)
            except InvalidSpecifier:
                found.append(FieldError("depends_on", "bad_range", f"{other}: {wanted!r}."))
        if self.id != CORE and CORE not in self.depends_on:
            found.append(FieldError("depends_on", "needs_core", "Every module depends on sys."))
        if self.id in self.depends_on:
            found.append(FieldError("depends_on", "itself", "A module can't depend on itself."))
        found += [
            FieldError("hooks", "not_its_table", f"{h.table} isn't one of this module's tables.")
            for h in self.hooks
            if not h.table.startswith(f"{self.id}.")
        ]
        if self.docs is not None and not self.docs.is_dir():
            found.append(FieldError("docs", "missing", f"No folder {self.docs}."))
        return found

    def check(self) -> None:
        found = self.problems()
        if found:
            raise Problem(
                422, "invalid_module", f"The module {self.id!r} can't be installed.", errors=found
            )
