"""No escalation: nobody gives themselves more rights than they were given.

- **Powerful roles** (sys.admin, sys.access_admin, module admin roles) are given to a group, or
  taken from it, only by members of a group holding sys.admin. The same goes for changing who is
  in a group holding a powerful role, or its rule: otherwise an access administrator could simply
  join the Administrators group.
- **Module roles are read-only**: they change only by releasing the module. Access
  administrators build custom roles instead.
- **Custom roles are made of what modules declare**: a grant only for a table and operation some
  module role grants. A custom role that copies a power only powerful roles have, or includes a
  powerful role, is powerful itself, and then only administrators may change it while a group
  holds it.

Every change to access (groups, memberships, roles, grants) also needs the second factor given
in the last 15 minutes, so a session left open or taken over can't hand out rights.

The system itself (installing, upgrading) is exempt.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.hooks import Change
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem

_HOLDS_ADMIN = text(
    """
WITH RECURSIVE held(role) AS (
    SELECT gr.role FROM sys.group_role gr JOIN sys."group" g ON g.sys_id = gr."group"
    WHERE gr."group" = ANY(:groups) AND gr.deleted IS NULL AND g.deleted IS NULL
  UNION
    SELECT ri.includes FROM sys.role_include ri JOIN held ON ri.role = held.role
    WHERE ri.deleted IS NULL
)
SELECT EXISTS (SELECT 1 FROM held JOIN sys.role r ON r.sys_id = held.role
               WHERE r.name = 'sys.admin' AND r.deleted IS NULL)
"""
)
_GROUP_IS_POWERFUL = text(
    "SELECT EXISTS (SELECT 1 FROM sys.group_role gr JOIN sys.role r ON r.sys_id = gr.role"
    ' WHERE gr."group" = :group AND gr.deleted IS NULL AND r.deleted IS NULL AND r.powerful)'
)
_POWERFUL = text("SELECT powerful FROM sys.role WHERE sys_id = :id")
_CUSTOM = text("SELECT custom FROM sys.role WHERE sys_id = :id")
_HELD = text("SELECT EXISTS (SELECT 1 FROM sys.group_role WHERE role = :id AND deleted IS NULL)")
_DECLARED = text(
    'SELECT EXISTS (SELECT 1 FROM sys."grant" g JOIN sys.role r ON r.sys_id = g.role'
    ' WHERE NOT r.custom AND r.deleted IS NULL AND g.deleted IS NULL AND g."table" = :table'
    " AND g.operation = :operation)"
)
# Powerful if one of its grants is a power no ordinary module role has, or it includes a
# powerful role.
_RECOMPUTE = text(
    """
UPDATE sys.role SET powerful = (
    EXISTS (
        SELECT 1 FROM sys."grant" g WHERE g.role = :id AND g.deleted IS NULL AND NOT EXISTS (
            SELECT 1 FROM sys."grant" m JOIN sys.role mr ON mr.sys_id = m.role
            WHERE NOT mr.custom AND NOT mr.powerful AND mr.deleted IS NULL AND m.deleted IS NULL
              AND m."table" = g."table" AND m.operation = g.operation))
    OR EXISTS (
        SELECT 1 FROM sys.role_include i JOIN sys.role ir ON ir.sys_id = i.includes
        WHERE i.role = :id AND i.deleted IS NULL AND ir.deleted IS NULL AND ir.powerful))
WHERE sys_id = :id
RETURNING powerful
"""
)


RECENT = timedelta(minutes=15)


def _refuse(code: str, title: str, detail: str | None = None) -> Problem:
    return Problem(403, code, title, detail=detail)


def recently_verified(change: Change) -> None:
    """Refuses an access change unless the second factor was given in the last minutes."""
    if change.records.access.unrestricted:
        return
    verified = current().verified_at
    if verified is None or datetime.now(UTC) - verified > RECENT:
        raise _refuse(
            "verify_again", "Enter a code from your authenticator app again to change access."
        )


async def _row(change: Change, table: str, *columns: str) -> dict[str, Any]:
    """The record as it is now, as the system sees it (the hook needs every field)."""
    if change.sys_id is None:
        return {}
    names = ", ".join(f'"{c}"' for c in columns)
    found = (
        await change.records.conn.execute(
            text(f"SELECT {names} FROM {table} WHERE sys_id = :id"),  # noqa: S608
            {"id": change.sys_id},
        )
    ).first()
    return dict(found._mapping) if found is not None else {}  # pyright: ignore[reportPrivateUsage]


def _values(change: Change, before: dict[str, Any], name: str) -> list[Any]:
    """The field's value before and after the change (both matter for a move)."""
    found = [before.get(name)]
    if name in change.values:
        found.append(change.values[name])
    return [v for v in found if v is not None]


async def holds_admin(conn: AsyncConnection, groups: frozenset[uuid.UUID]) -> bool:
    """Whether one of these groups holds sys.admin (directly or through an included role).

    On the caller's connection as it is: the core's role, or the owner's.
    """
    return bool(await conn.scalar(_HOLDS_ADMIN, {"groups": list(groups)}))


async def _is_admin(change: Change) -> bool:
    access = change.records.access
    return access.unrestricted or await holds_admin(change.records.conn, access.groups)


async def _admins_only(change: Change, why: str) -> None:
    if not await _is_admin(change):
        raise _refuse(
            "admins_only",
            "Only members of a group holding the administrator role may do this.",
            why,
        )


async def group_role(change: Change) -> None:
    """Giving a powerful role to a group, or taking it away."""
    recently_verified(change)
    await switch_role(change.records.conn, "sys")
    before = await _row(change, "sys.group_role", "role")
    group = change.values.get("group", (await _row(change, "sys.group_role", "group")).get("group"))
    synced = group is not None and await change.records.conn.scalar(
        text('SELECT provider IS NOT NULL FROM sys."group" WHERE sys_id = :g'), {"g": group}
    )
    for role in _values(change, before, "role"):
        if await change.records.conn.scalar(_POWERFUL, {"id": role}):
            if synced and change.operation != "delete":
                raise _refuse(
                    "provider_group",
                    "A group following a sign-in provider can't hold a powerful role.",
                )
            await _admins_only(change, "The role is powerful.")


async def membership(change: Change) -> None:
    """Joining or leaving a group that holds a powerful role, and members of rule-based groups."""
    recently_verified(change)
    await switch_role(change.records.conn, "sys")
    before = await _row(change, "sys.membership", "group", "from_rule")
    if before.get("from_rule") or change.values.get("from_rule"):
        raise Problem(409, "from_rule", "Members of a group with a rule come from the rule only.")
    for group in _values(change, before, "group"):
        if await change.records.conn.scalar(
            text(
                'SELECT rule IS NOT NULL OR provider IS NOT NULL FROM sys."group" WHERE sys_id = :g'
            ),
            {"g": group},
        ):
            raise Problem(
                409,
                "from_rule",
                "Members of this group come from its rule or its sign-in provider only.",
            )
        if await change.records.conn.scalar(_GROUP_IS_POWERFUL, {"group": group}):
            await _admins_only(change, "The group holds a powerful role.")


async def group(change: Change) -> None:
    """A group holding a powerful role: its rule and its existence are for administrators; it
    can't start following a provider."""
    recently_verified(change)
    if change.sys_id is not None and change.values.get("provider") is not None:
        await switch_role(change.records.conn, "sys")
        if await change.records.conn.scalar(_GROUP_IS_POWERFUL, {"group": change.sys_id}):
            raise _refuse(
                "provider_group", "A group holding a powerful role can't follow a provider."
            )
    if change.sys_id is None or (change.operation == "write" and "rule" not in change.values):
        return
    await switch_role(change.records.conn, "sys")
    if await change.records.conn.scalar(_GROUP_IS_POWERFUL, {"group": change.sys_id}):
        await _admins_only(change, "The group holds a powerful role.")


async def role(change: Change) -> None:
    """Only custom roles change in the app; they start ordinary and become powerful by grants."""
    if change.records.access.unrestricted:
        return
    recently_verified(change)
    await switch_role(change.records.conn, "sys")
    if change.operation == "create":
        change.values.update(custom=True, powerful=False, module="custom", switched_off=False)
        return
    if not await change.records.conn.scalar(_CUSTOM, {"id": change.sys_id}):
        raise _refuse("declared_role", "Roles of modules change only with the module.")
    if {"custom", "powerful", "module", "switched_off"} & change.values.keys():
        raise _refuse("system_field", "Whether a role is custom or powerful isn't set by hand.")
    if await change.records.conn.scalar(_POWERFUL, {"id": change.sys_id}):
        await _admins_only(change, "The role is powerful.")


async def _custom_parts(change: Change, table: str) -> uuid.UUID | None:
    """The custom role a grant or an included role belongs to; refuses a module's role.

    None if no role is given yet: then checking the values refuses the change anyway.
    """
    await switch_role(change.records.conn, "sys")
    before = await _row(change, table, "role")
    roles = _values(change, before, "role")
    for found in roles:
        if not await change.records.conn.scalar(_CUSTOM, {"id": found}):
            raise _refuse("declared_role", "Roles of modules change only with the module.")
    return roles[-1] if roles else None


async def grant(change: Change) -> None:
    if change.records.access.unrestricted:
        return
    recently_verified(change)
    owner = await _custom_parts(change, 'sys."grant"')
    if owner is None:
        return
    before = await _row(change, 'sys."grant"', "table", "operation")
    table = change.values.get("table", before.get("table"))
    operation = change.values.get("operation", before.get("operation"))
    if not await change.records.conn.scalar(_DECLARED, {"table": table, "operation": operation}):
        raise _refuse(
            "not_declared",
            "Custom roles are made of what modules grant; no module grants this.",
        )
    if await change.records.conn.scalar(_POWERFUL, {"id": owner}):
        await _admins_only(change, "The role is powerful.")


async def role_include(change: Change) -> None:
    if change.records.access.unrestricted:
        return
    recently_verified(change)
    owner = await _custom_parts(change, "sys.role_include")
    if owner is not None and await change.records.conn.scalar(_POWERFUL, {"id": owner}):
        await _admins_only(change, "The role is powerful.")


async def recompute(change: Change) -> None:
    """After a custom role's grants or included roles change: is it powerful now?"""
    if change.records.access.unrestricted:
        return
    await switch_role(change.records.conn, "sys")
    table = 'sys."grant"' if change.table.name == "grant" else "sys.role_include"
    owner = (await _row(change, table, "role")).get("role")
    powerful = await change.records.conn.scalar(_RECOMPUTE, {"id": owner})
    if powerful and await change.records.conn.scalar(_HELD, {"id": owner}):
        await _admins_only(change, "The role is now powerful and groups hold it.")
