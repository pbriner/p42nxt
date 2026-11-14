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
from p42cloud.data.hooks import EVERY_TABLE, TableHook
from p42cloud.data.settings import Setting
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.roles import Role
from p42cloud.ui.actions.models import UIAction
from p42cloud.ui.forms.models import FormView
from p42cloud.ui.navigation.models import Entry
from p42cloud.ui.rules.models import FormRule
from p42cloud.ui.themes.models import Theme
from p42cloud.ui.views.models import ListView

# Called as the owner after the module's tables and roles are written, with the catalog.
type Seed = Callable[[AsyncConnection, Catalog], Awaitable[None]]


@dataclass(frozen=True)
class Migration:
    """Moves data when upgrading to `version` (tables come from the declarations, not from here).

    Runs once, on the upgrade that passes its version, after the tables are adjusted and in the
    same transaction: if it fails, the upgrade didn't happen.
    """

    version: str
    run: Callable[[AsyncConnection], Awaitable[None]]


MODULE_ID = re.compile(r"^[a-z][a-z0-9]{1,15}$")
ICON = re.compile(r"^[a-z0-9_]{1,64}$")
MAX_CATEGORY = 40
CORE = "sys"
RESERVED = frozenset({"u", "search"})  # admins' own tables; a route of its own


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
    migrations: tuple[Migration, ...] = ()
    settings: tuple[Setting, ...] = ()
    # Where it shows in the navigator, and what it shows there.
    category: str = "General"
    icon: str = "extension"
    navigation: tuple[Entry, ...] = ()
    views: tuple[ListView, ...] = ()
    forms: tuple[FormView, ...] = ()
    actions: tuple[UIAction, ...] = ()
    rules: tuple[FormRule, ...] = ()
    themes: tuple[Theme, ...] = ()

    @property
    def parsed_version(self) -> Version:
        return Version(self.version)

    def _place_problems(self) -> list[FieldError]:
        if 1 <= len(self.category) <= MAX_CATEGORY and ICON.match(self.icon):
            return []
        return [FieldError("category", "bad_place", "A category of 1 to 40 characters, an icon.")]

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
            FieldError("settings", "not_its_setting", f"{s.name} must start with {self.id}.")
            for s in self.settings
            if not s.name.startswith(f"{self.id}.")
        ]
        found += [
            FieldError("hooks", "not_its_table", f"{h.table} isn't one of this module's tables.")
            for h in self.hooks
            # Only the core watches every table (EVERY_TABLE).
            if not h.table.startswith(f"{self.id}.")
            and not (self.id == CORE and h.table == EVERY_TABLE)
        ]
        for migration in self.migrations:
            try:
                Version(migration.version)
            except InvalidVersion:
                found.append(
                    FieldError(
                        "migrations", "bad_version", f"Not a version: {migration.version!r}."
                    )
                )
        found += self._place_problems()
        if self.docs is not None and not self.docs.is_dir():
            found.append(FieldError("docs", "missing", f"No folder {self.docs}."))
        return found

    def check(self) -> None:
        found = self.problems()
        if found:
            raise Problem(
                422, "invalid_module", f"The module {self.id!r} can't be installed.", errors=found
            )
