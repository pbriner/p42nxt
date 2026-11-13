"""Access in SQL: which records, and which of their fields, a person may read or change.

The permits come from the person's grants (worked out by `security`, handed over in the
context). For an operation on a table they become one SQL condition for the records, and one per
field: a field counts on a record only where a grant that covers the field also matches the
record. So a grant for every field of one's own record never opens those fields on other records
that another grant lets one see.

Deny by default: no permit, no access. A permit whose condition can't be built any more (its
field was retired) grants nothing.
"""

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import SYSTEM_FIELDS, Filter, Query, Situation
from p42cloud.kernel.context import Access, Permit
from p42cloud.kernel.errors import Problem


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
