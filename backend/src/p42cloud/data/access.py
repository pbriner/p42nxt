"""Access in SQL: which records, and which of their fields, a person may read or change.

The permits come from the person's grants (worked out by `security`, handed over in the
context). For an operation on a table they become one SQL condition for the records, and one per
field: a field counts on a record only where a grant that covers the field also matches the
record. So a grant for every field of one's own record never opens those fields on other records
that another grant lets one see.

Deny by default: no permit, no access. A permit whose condition can't be built any more (its
field was retired) grants nothing.

Personal tables (budgets, portfolios) name each record's owner. Only the owner and those they
share a record with see it, whatever the grants say: no group, role or admin overrides that, only
the system itself (installing, its own jobs). Shares are records of `sys.share`: read or edit,
optionally until a time.
"""

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import SYSTEM_FIELDS, Filter, Query, Situation
from p42cloud.data.declarations import Table
from p42cloud.data.sql import ident
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.reference import Reference
from p42cloud.kernel.context import Access, Permit, current
from p42cloud.kernel.errors import Problem

SHARE = Table(
    "share",
    "Share",
    {
        "table": Reference("Table", "sys.table", required=True),
        "record": AnyRecord("Record", required=True),
        "person": Reference("Shared with", "sys.person", required=True),
        "edit": Boolean("May change it", required=True, default=False),
        "until": DateTime("Until"),
        # Set by the system to the record's owner when the share is made.
        "owner": Reference("Shared by", "sys.person"),
    },
    label_plural="Shares",
    unique_together=(("table", "record", "person"),),
)
CHANGES = frozenset({"write", "delete", "restore"})


def forbidden(detail: str | None = None) -> Problem:
    return Problem(403, "forbidden", "You may not do this.", detail=detail)


def _covers(permit: Permit, name: str | None) -> bool:
    """Whether a permit reaches a field (None: the record itself). System fields come with it."""
    return name is None or name in SYSTEM_FIELDS or permit.fields is None or name in permit.fields


class Rules:
    """The permits of one person, as SQL for one statement."""

    def __init__(
        self, catalog: Catalog, situation: Situation, access: Access, query: Query
    ) -> None:
        self.catalog = catalog
        self.situation = situation
        self.access = access
        self.query = query
        self._built: dict[tuple[str, str, str, int], str | None] = {}
        self._me: str | None = None

    def _personal(
        self, table: TableDef, alias: str, operation: str, name: str | None
    ) -> str | None:
        """SQL limiting a personal table to its owner and those it's shared with; None if not.

        Only the owner makes records and hands them on: someone a record is shared with, even to
        change it, can't change whose it is.
        """
        if table.owner_field is None or self.access.unrestricted:
            return None
        # A module's own jobs work for everyone on its personal tables (a buy for each plan's
        # owner), as its service account; nobody else's tables, and only within its grants.
        if table.schema != "sys" and current().channel.startswith(f"job:{table.schema}."):
            return None
        if self._me is None:
            self._me = self.query.bind(self.situation.person)
        owner = f"{alias}.{ident(table.owner_field)} = {self._me}"
        if operation == "create" or (operation in CHANGES and name == table.owner_field):
            return owner
        shared = (
            f"sys.p42_shared({self.query.bind(table.key)}, {alias}.sys_id, {self._me},"
            f" {'true' if operation in CHANGES else 'false'})"
        )
        return f"({owner} OR {shared})"

    def allows(self, table: TableDef, operation: str) -> bool:
        """Whether any grant allows the operation on the table at all."""
        return bool(self.access.of(table.key, operation))

    def _condition(
        self, table: TableDef, alias: str, operation: str, number: int, permit: Permit
    ) -> str | None:
        """The permit's condition for `alias`; None if it can't be built (it grants nothing)."""
        key = (table.key, alias, operation, number)
        if key not in self._built:
            if permit.condition is None or permit.condition == {}:
                self._built[key] = "true"
            else:
                try:
                    filtering = Filter(self.catalog, self.situation, self.query)
                    built = filtering.sql(table, alias, permit.condition)
                    self._built[key] = f"COALESCE({built}, false)"
                except Problem:
                    self._built[key] = None
        return self._built[key]

    def _any(self, table: TableDef, alias: str, operation: str, name: str | None) -> str:
        allowed = self._granted(table, alias, operation, name)
        personal = self._personal(table, alias, operation, name)
        if personal is None or allowed == "false":
            return allowed
        return personal if allowed == "true" else f"({allowed} AND {personal})"

    def _granted(self, table: TableDef, alias: str, operation: str, name: str | None) -> str:
        parts: list[str] = []
        for number, permit in enumerate(self.access.of(table.key, operation)):
            if not _covers(permit, name):
                continue
            condition = self._condition(table, alias, operation, number, permit)
            if condition == "true":
                return "true"
            if condition is not None:
                parts.append(condition)
        return f"({' OR '.join(parts)})" if parts else "false"

    def rows(self, table: TableDef, alias: str, operation: str = "read") -> str:
        """SQL: the records of `table` (as `alias`) the operation is allowed on."""
        return self._any(table, alias, operation, None)

    def field(self, table: TableDef, alias: str, name: str, operation: str = "read") -> str:
        """SQL: whether the operation is allowed on field `name` of that record."""
        return self._any(table, alias, operation, name)
