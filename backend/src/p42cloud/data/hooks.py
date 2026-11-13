"""Hooks: what upper layers do when a table's records change, through the records service.

A hook registered *before* a change sees the values about to be written and may refuse the change
(raise a Problem) or add values the system sets itself. One registered *after* runs in the same
transaction, so its work and the change succeed or fail together. The upper layers register
their hooks here; this layer never knows who they are.

Changes made with SQL outside the records service don't run hooks; whatever they keep in step is
repaired by jobs.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from p42cloud.data.catalog import TableDef
from p42cloud.kernel.db import switch_role

if TYPE_CHECKING:
    from p42cloud.data.records import Records


@dataclass
class Change:
    records: Records
    table: TableDef
    operation: str  # create, write, delete, restore
    sys_id: UUID | None  # None before a create
    # The values given (create, write); a before hook may add or change them.
    values: dict[str, Any] = field(default_factory=dict[str, Any])


Hook = Callable[[Change], Awaitable[None]]


@dataclass(frozen=True)
class TableHook:
    """What a feature or module declares: code to run when one table's records change."""

    table: str
    before: Hook | None = None
    after: Hook | None = None


@dataclass
class Hooks:
    before: dict[str, list[Hook]] = field(default_factory=dict[str, list[Hook]])
    after: dict[str, list[Hook]] = field(default_factory=dict[str, list[Hook]])

    def on(self, table: str, *, before: Hook | None = None, after: Hook | None = None) -> None:
        """Registers hooks for a table's changes; registering the same one again does nothing."""
        for registry, hook in ((self.before, before), (self.after, after)):
            if hook is not None and hook not in registry.setdefault(table, []):
                registry[table].append(hook)

    async def run(self, when: str, change: Change) -> None:
        hooks = (self.before if when == "before" else self.after).get(change.table.key, [])
        for hook in hooks:
            await hook(change)
        if hooks:  # hooks may work as another module; the change goes on as the table's
            await switch_role(change.records.conn, change.table.schema)

    def add(self, *declared: TableHook) -> None:
        for hook in declared:
            self.on(hook.table, before=hook.before, after=hook.after)


HOOKS = Hooks()
