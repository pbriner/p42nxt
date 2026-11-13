"""Groups with a rule: their members are the active people the rule matches. Never service
accounts: what they may do is given to them by hand, never by who they happen to resemble.

The memberships are records marked "from the rule", kept in step in the same transaction as the
change that moves someone in or out: a person's fields changing, a group's rule changing, a
person becoming inactive. So access checks stay plain SQL, and every move is audited.

A rule uses only the person's own fields, and none a person may change about themselves (their
profile): otherwise anyone could put themselves into a group and get its roles. Variables about
the person looking (`@me`, `@my_groups`, `@person`) mean nothing here and are refused.
"""

import uuid
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.conditions import Filter, Query, Situation
from p42cloud.data.hooks import Change
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.people.models import SELF_SERVICE

PERSON = "sys.person"
_ABOUT_VIEWER = ("@me", "@my_groups", "@person")


def _refuse(message: str) -> Problem:
    return Problem(
        422,
        "invalid_rule",
        "The group's rule can't be used.",
        errors=[FieldError("rule", "invalid_rule", message)],
    )


def _walk(node: Any, paths: list[str], texts: list[str]) -> None:
    """Collects the field paths and every text anywhere in a condition."""
    if isinstance(node, dict):
        found = cast(dict[str, Any], node)
        if "field" in found:
            paths.append(str(found["field"]))
        for child in found.values():
            _walk(child, paths, texts)
    elif isinstance(node, list):
        for child in cast(list[Any], node):
            _walk(child, paths, texts)
    elif isinstance(node, str):
        texts.append(node)


def check_rule(catalog: Catalog, rule: Any) -> None:
    """Refuses a rule people could game, or one that can't be built."""
    paths: list[str] = []
    texts: list[str] = []
    _walk(rule, paths, texts)
    for path in paths:
        if "." in path:
            raise _refuse(f"{path}: only the person's own fields can be used.")
        if path in SELF_SERVICE:
            raise _refuse(f"{path}: people change it themselves, so it can't decide groups.")
    if any(t.startswith(_ABOUT_VIEWER) for t in texts):
        raise _refuse("@me, @my_groups and @person mean nothing in a group's rule.")
    table = catalog.table(PERSON)
    if table is None:
        raise RuntimeError("no person table")
    Filter(catalog, Situation(uuid.UUID(int=0)), Query()).sql(table, "t", rule)


async def _matching(
    conn: AsyncConnection, catalog: Catalog, rule: Any, person: uuid.UUID | None
) -> set[uuid.UUID]:
    table = catalog.table(PERSON)
    if table is None:
        raise RuntimeError("no person table")
    query = Query()
    condition = Filter(catalog, Situation(uuid.UUID(int=0)), query).sql(table, "t", rule)
    only = f" AND t.sys_id = {query.bind(person)}" if person is not None else ""
    found = await conn.execute(
        text(
            "SELECT t.sys_id FROM sys.person t WHERE t.deleted IS NULL AND t.active"  # noqa: S608
            " AND t.kind <> 'service'"
            f" AND {condition}{only}"
        ),
        query.params,
    )
    return set(found.scalars())


async def _sync(
    conn: AsyncConnection, group: uuid.UUID, members: set[uuid.UUID], person: uuid.UUID | None
) -> None:
    """Brings the group's memberships (or one person's) to `members`."""
    only = " AND person = :person" if person is not None else ""
    rows = (
        await conn.execute(
            text(
                "SELECT sys_id, person, from_rule, deleted IS NOT NULL AS gone"  # noqa: S608
                f' FROM sys.membership WHERE "group" = :group{only} ORDER BY sys_id'
            ),
            {"group": group, "person": person},
        )
    ).all()
    active = {r.person: r for r in rows if not r.gone}
    gone = {r.person: r for r in rows if r.gone}
    for member in members:
        if member in active:
            if not active[member].from_rule:
                await conn.execute(
                    text("UPDATE sys.membership SET from_rule = true WHERE sys_id = :id"),
                    {"id": active[member].sys_id},
                )
        elif member in gone:
            await conn.execute(
                text(
                    "UPDATE sys.membership SET deleted = NULL, from_rule = true WHERE sys_id = :id"
                ),
                {"id": gone[member].sys_id},
            )
        else:
            await conn.execute(
                text(
                    'INSERT INTO sys.membership ("group", person, from_rule)'
                    " VALUES (:group, :person, true)"
                ),
                {"group": group, "person": member},
            )
    leaving = [r.sys_id for p, r in active.items() if p not in members]
    await conn.execute(
        text("UPDATE sys.membership SET deleted = now() WHERE sys_id = ANY(:ids)"),
        {"ids": leaving},
    )


async def recalculate_group(conn: AsyncConnection, catalog: Catalog, group: uuid.UUID) -> None:
    """All members of one group, from its rule; a group without a rule keeps its members.

    Works on the caller's connection as it is: the core's role, or the owner while installing.
    """
    row = (
        await conn.execute(
            text(
                'SELECT rule, provider, deleted IS NOT NULL AS gone FROM sys."group"'
                " WHERE sys_id = :g"
            ),
            {"g": group},
        )
    ).first()
    if row is None or row.provider is not None:
        return  # a provider's group follows the provider, at sign-in
    if row.rule is None:
        # Static now: the members stay, kept by hand from here on.
        await conn.execute(
            text(
                'UPDATE sys.membership SET from_rule = false WHERE "group" = :g AND from_rule'
                " AND deleted IS NULL"
            ),
            {"g": group},
        )
        return
    members: set[uuid.UUID] = set()
    if not row.gone:
        members = await _matching(conn, catalog, row.rule, None)
    await _sync(conn, group, members, None)


async def recalculate_person(conn: AsyncConnection, catalog: Catalog, person: uuid.UUID) -> None:
    """One person's place in every group with a rule (on the core's role or the owner's)."""
    groups = (
        await conn.execute(
            text('SELECT sys_id, rule FROM sys."group" WHERE rule IS NOT NULL AND deleted IS NULL')
        )
    ).all()
    for group in groups:
        members = await _matching(conn, catalog, group.rule, person)
        await _sync(conn, group.sys_id, members, person)


async def check_group(change: Change) -> None:
    """Before a group is saved: its rule must be one people can't game, and its members come
    from a rule or from a provider, not both."""
    rule = change.values.get("rule")
    if rule is not None:
        check_rule(change.records.catalog, rule)
    before: dict[str, object] = {}
    if change.sys_id is not None:
        await switch_role(change.records.conn, "sys")
        found = (
            await change.records.conn.execute(
                text('SELECT rule, provider FROM sys."group" WHERE sys_id = :g'),
                {"g": change.sys_id},
            )
        ).first()
        before = dict(found._mapping) if found else {}  # pyright: ignore[reportPrivateUsage]
    merged = before | change.values
    if merged.get("rule") is not None and merged.get("provider") is not None:
        raise Problem(422, "validation", "Members come from a rule or from a provider, not both.")


async def sync_from_provider(
    conn: AsyncConnection, provider: uuid.UUID, person: uuid.UUID, names: set[str]
) -> None:
    """The person's place in every group following this provider, from its groups claim."""
    groups = (
        await conn.execute(
            text(
                'SELECT sys_id, provider_group FROM sys."group" WHERE provider = :p'
                " AND deleted IS NULL"
            ),
            {"p": provider},
        )
    ).all()
    for group in groups:
        members = {person} if group.provider_group in names else set[uuid.UUID]()
        await _sync(conn, group.sys_id, members, person)


async def group_changed(change: Change) -> None:
    if change.sys_id is not None:
        await switch_role(change.records.conn, "sys")
        await recalculate_group(change.records.conn, change.records.catalog, change.sys_id)


async def person_changed(change: Change) -> None:
    if change.sys_id is not None:
        await switch_role(change.records.conn, "sys")
        await recalculate_person(change.records.conn, change.records.catalog, change.sys_id)
