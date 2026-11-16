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
from importlib import metadata
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
from p42cloud.services.calls.models import OutsideCall
from p42cloud.services.connections.models import ConnectionType
from p42cloud.services.events.models import EventType, Subscription
from p42cloud.services.jobs.models import Job, Schedule
from p42cloud.services.notifications.models import NotificationKind
from p42cloud.services.routes.models import Route
from p42cloud.ui.actions.models import UIAction
from p42cloud.ui.forms.models import FormView
from p42cloud.ui.navigation.models import Entry
from p42cloud.ui.rules.models import FormRule
from p42cloud.ui.themes.models import Theme
from p42cloud.ui.views.models import ListView
from p42cloud.ui.widgets.models import Widget

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
PUBLISHER = re.compile(r"^[a-z][a-z0-9]{1,30}$")
PLATFORM = "p42"  # the platform's own publisher: the core and its own modules
# IDs no module may take: admins' own tables (`u`), the platform's names, and every first part of
# the core's own API addresses (modules live at /api/v1/<id>/…; a test keeps this complete).
RESERVED = frozenset(
    {
        "u",
        "core",
        "p42",
        "p42cloud",
        "api",
        "admin",
        "auth",
        "app",
        "apps",
        "portal",
        "public",
        "static",
        "docs",
        "files",
        "help",
        "www",
        "dashboards",
        "health",
        "installation",
        "languages",
        "mcp",
        "me",
        "meta",
        "navigation",
        "people",
        "problems",
        "search",
        "setup",
        "tables",
        "theme",
        "undo",
        "verify",
    }
)


@dataclass(frozen=True)
class Module:
    id: str
    name: str
    version: str
    # Who makes it (`p42` for the platform's own): updates come only from them, and an ID one
    # publisher has installed can't be taken by another. No default to claim; required to
    # install a new module and to publish one.
    publisher: str = ""
    # Other modules it needs, with the versions it works with: {"sys": ">=0.1,<1"}.
    depends_on: Mapping[str, str] = field(default_factory=dict[str, str])
    tables: tuple[Table, ...] = ()
    roles: tuple[Role, ...] = ()
    hooks: tuple[TableHook, ...] = ()
    # A folder of Markdown files: the module's documentation, written into the wiki's pages.
    docs: Path | None = None
    # A folder of <language>.json files: translations of what the module defines (the format
    # the languages export writes).
    translations: Path | None = None
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
    widgets: tuple[Widget, ...] = ()
    jobs: tuple[Job, ...] = ()
    schedules: tuple[Schedule, ...] = ()
    connection_types: tuple[ConnectionType, ...] = ()
    calls: tuple[OutsideCall, ...] = ()
    # What it tells other modules about, and what it listens for.
    events: tuple[EventType, ...] = ()
    subscriptions: tuple[Subscription, ...] = ()
    notification_kinds: tuple[NotificationKind, ...] = ()
    routes: tuple[Route, ...] = ()
    themes: tuple[Theme, ...] = ()

    @property
    def parsed_version(self) -> Version:
        return Version(self.version)

    def _place_problems(self) -> list[FieldError]:
        if 1 <= len(self.category) <= MAX_CATEGORY and ICON.match(self.icon):
            return []
        return [FieldError("category", "bad_place", "A category of 1 to 40 characters, an icon.")]

    def _name_problems(self) -> list[FieldError]:
        if not MODULE_ID.match(self.id):
            found = [FieldError("id", "bad_id", "2 to 16 of a-z and 0-9, starting with a letter.")]
        elif self.id in RESERVED or (self.id == CORE and self.publisher != PLATFORM):
            found = [FieldError("id", "reserved", f"{self.id!r} is kept for the platform.")]
        else:
            found = []
        # Empty is allowed here: modules installed before publishers keep loading until their
        # next update. A new install and publishing a release require one.
        if self.publisher and not PUBLISHER.match(self.publisher):
            found.append(
                FieldError("publisher", "bad_publisher", "Who makes it: 2 to 31 of a-z and 0-9.")
            )
        return found

    def problems(self) -> list[FieldError]:
        """What's wrong with the manifest itself, before anything is installed."""
        found: list[FieldError] = self._name_problems()
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
        found += self._folder_problems()
        return found

    def _folder_problems(self) -> list[FieldError]:
        return [
            FieldError(name, "missing", f"No folder {folder}.")
            for name, folder in (("docs", self.docs), ("translations", self.translations))
            if folder is not None and not folder.is_dir()
        ]

    def check(self) -> None:
        found = self.problems()
        if found:
            raise Problem(
                422, "invalid_module", f"The module {self.id!r} can't be installed.", errors=found
            )


def package_version(distribution: str) -> str:
    """A module's version as its package has it (`pyproject.toml`), for `Module(version=…)`: one
    place to raise it, so the manifest can't say another version than the package."""
    return metadata.version(distribution)
