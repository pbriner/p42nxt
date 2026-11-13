"""Working out what a person may do: their groups, the groups' roles, those roles' grants.

Read fresh for every request (it's one query), so a change to a group or a role counts at once.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.kernel.context import NOTHING, Access, Permit
from p42cloud.kernel.db import switch_role

_GROUPS = text(
    'SELECT m."group" FROM sys.membership m JOIN sys."group" g ON g.sys_id = m."group"'
    " JOIN sys.person p ON p.sys_id = m.person"
    " WHERE m.person = :person AND m.deleted IS NULL AND g.deleted IS NULL"
    " AND p.deleted IS NULL AND p.active"
)

# Roles held through the groups, with the group's narrowing, and every role they include
# (UNION drops repeats, so a loop that slipped in still ends).
_GRANTS = text(
    """
WITH RECURSIVE held(role, narrowing) AS (
    SELECT gr.role, gr.condition FROM sys.group_role gr
    WHERE gr."group" = ANY(:groups) AND gr.deleted IS NULL
  UNION
    SELECT ri.includes, held.narrowing FROM sys.role_include ri
    JOIN held ON ri.role = held.role WHERE ri.deleted IS NULL
),
-- An API token may be limited to some roles: those, and the roles they include.
allowed(role) AS (
    SELECT sys_id FROM sys.role WHERE name = ANY(:only) AND deleted IS NULL
  UNION
    SELECT ri.includes FROM sys.role_include ri JOIN allowed ON ri.role = allowed.role
    WHERE ri.deleted IS NULL
)
SELECT t."schema" || '.' || t.name AS table_key, g.operation, g.condition, g.fields,
       held.narrowing
FROM held
JOIN sys.role r ON r.sys_id = held.role AND r.deleted IS NULL AND NOT r.switched_off
    AND (:everything OR r.sys_id IN (SELECT role FROM allowed))
JOIN sys."grant" g ON g.role = r.sys_id AND g.deleted IS NULL
JOIN sys."table" t ON t.sys_id = g."table" AND t.deleted IS NULL AND NOT t.switched_off
"""
)


def _both(*conditions: Any) -> Any:
    given = [c for c in conditions if c is not None and c != {}]
    if not given:
        return None
    return given[0] if len(given) == 1 else {"and": given}


async def access_for(
    conn: AsyncConnection, person: uuid.UUID, only: frozenset[str] | None = None
) -> Access:
    """Everything the person may do; nothing if they're inactive or unknown.

    `only` limits it to these roles (by name) and the roles they include, as far as the person
    holds them: what an API token naming its roles gets. Never more than the person has.
    """
    await switch_role(conn, "sys")
    groups = frozenset((await conn.execute(_GROUPS, {"person": person})).scalars())
    if not groups:
        return NOTHING
    permits: dict[tuple[str, str], list[Permit]] = {}
    found = await conn.execute(
        _GRANTS,
        {"groups": list(groups), "only": sorted(only or ()), "everything": only is None},
    )
    for row in found:
        fields = frozenset(row.fields) if row.fields is not None else None
        permit = Permit(_both(row.condition, row.narrowing), fields)
        found = permits.setdefault((row.table_key, row.operation), [])
        if permit not in found:
            found.append(permit)
    return Access(groups, {key: tuple(found) for key, found in permits.items()})
