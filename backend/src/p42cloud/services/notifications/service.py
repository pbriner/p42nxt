"""Sending notifications in the sender's transaction, and keeping the inbox tidy.

A notification exists only if the change that sends it is kept. Recipients are the people named
plus everyone holding the roles named (through their groups, and roles that include them):
active people only, never service accounts.
"""

import json
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.declared import label_of, sync
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.services.notifications.models import CHANNELS, SEVERITIES, NotificationKind

KIND_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_TITLE = 200
MAX_MESSAGE = 2000
MAX_LINK = 500
MAX_RECIPIENTS = 10_000
KEEP_READ = timedelta(days=90)
KEEP_UNREAD = timedelta(days=365)

# The installed modules' kinds, by name: the module declaring each. Filled when composing.
KINDS: dict[str, str] = {}


def register(module: str, kinds: tuple[NotificationKind, ...]) -> None:
    for kind in kinds:
        KINDS[f"{module}.{kind.name}"] = module


async def write_kinds(
    conn: AsyncConnection, module: str, kinds: tuple[NotificationKind, ...]
) -> None:
    found = [
        FieldError(f"notification_kinds.{k.name}", "bad_name", "a-z, 0-9 and _, from a letter.")
        for k in kinds
        if not KIND_NAME.match(k.name)
    ]
    found += [
        FieldError(
            f"notification_kinds.{k.name}", "bad_channel", f"Channels: {', '.join(CHANNELS)}."
        )
        for k in kinds
        if not set(k.channels) <= set(CHANNELS)
    ]
    if len({k.name for k in kinds}) != len(kinds):
        found.append(FieldError("notification_kinds", "duplicate", "A kind's name is used twice."))
    if found:
        raise Problem(422, "invalid_kinds", "The notification kinds can't be used.", errors=found)
    before = {
        row.name: (row.channels, row.declared_channels)
        for row in await conn.execute(
            text(
                "SELECT name, channels, declared_channels FROM sys.notification_kind"
                " WHERE module = :m"
            ),
            {"m": module},
        )
    }
    rows = [
        {
            "name": f"{module}.{k.name}",
            "label": label_of(k.label),
            "description": label_of(k.description) if k.description is not None else None,
            "declared_channels": list(k.channels),
        }
        for k in kinds
    ]
    await sync(
        conn,
        "sys.notification_kind",
        module,
        rows,
        frozenset({"label", "description", "declared_channels"}),
    )
    for row in rows:
        chosen, suggested = before.get(row["name"], (None, None))
        # A new kind, or one whose channels nobody changed, takes the module's suggestion.
        if chosen is None or chosen == suggested:
            await conn.execute(
                text(
                    "UPDATE sys.notification_kind SET channels = CAST(:c AS jsonb) WHERE name = :n"
                ),
                {"c": json.dumps(row["declared_channels"]), "n": row["name"]},
            )


# Holders of any of the roles: through a group holding it, or a role that includes it.
_HOLDERS = text(
    """
WITH RECURSIVE wanted(role) AS (
    SELECT sys_id FROM sys.role WHERE name = ANY(:roles) AND deleted IS NULL
  UNION
    SELECT ri.role FROM sys.role_include ri JOIN wanted ON ri.includes = wanted.role
    WHERE ri.deleted IS NULL
)
SELECT DISTINCT m.person FROM sys.group_role gr
JOIN wanted ON wanted.role = gr.role
JOIN sys.membership m ON m."group" = gr."group" AND m.deleted IS NULL
JOIN sys."group" g ON g.sys_id = gr."group" AND g.deleted IS NULL
WHERE gr.deleted IS NULL
"""
)

_ACTIVE = text(
    "SELECT sys_id FROM sys.person WHERE sys_id = ANY(:people) AND deleted IS NULL AND active"
    " AND kind <> 'service'"
)


def _checked_link(link: str | None) -> str | None:
    """Only paths inside the app: a notification can't send anyone to another site."""
    if link is None:
        return None
    if (
        len(link) > MAX_LINK
        or not link.startswith("/")
        or link.startswith("//")
        or "\\" in link
        or any(ord(c) < 0x20 for c in link)  # noqa: PLR2004 - control characters
    ):
        raise Problem(422, "validation", "A notification links to a page of the app (/…).")
    return link


async def recipients(
    conn: AsyncConnection, people: Iterable[uuid.UUID], roles: Iterable[str]
) -> list[uuid.UUID]:
    """The active, real people among those named and the roles' holders."""
    await switch_role(conn, "sys")
    wanted = set(people)
    names = sorted(set(roles))
    if names:
        wanted |= set((await conn.execute(_HOLDERS, {"roles": names})).scalars())
    if not wanted:
        return []
    return sorted((await conn.execute(_ACTIVE, {"people": list(wanted)})).scalars())


async def send(  # noqa: PLR0913 - what a notification says, all by keyword
    conn: AsyncConnection,
    kind: str,
    title: str,
    *,
    people: Iterable[uuid.UUID] = (),
    roles: Iterable[str] = (),
    message: str | None = None,
    link: str | None = None,
    severity: str = "info",
) -> int:
    """Notifies the people in the caller's transaction (`conn`); returns how many."""
    if kind not in KINDS:
        raise Problem(404, "no_kind", "There's no such notification kind.", detail=kind)
    if severity not in SEVERITIES:
        raise Problem(422, "validation", f"The severity is one of {', '.join(SEVERITIES)}.")
    if not title.strip() or len(title) > MAX_TITLE:
        raise Problem(422, "validation", f"A title of 1 to {MAX_TITLE} characters.")
    if message is not None and len(message) > MAX_MESSAGE:
        raise Problem(422, "validation", f"A message of at most {MAX_MESSAGE} characters.")
    target = _checked_link(link)
    found = await recipients(conn, people, roles)
    if len(found) > MAX_RECIPIENTS:
        raise Problem(422, "too_many", f"At most {MAX_RECIPIENTS} people at once.")
    if not found:
        return 0
    made = list(
        await conn.execute(
            text(
                "INSERT INTO sys.notification (person, kind, title, message, link, severity, read)"
                " SELECT p, :kind, :title, :message, :link, :severity, false"
                " FROM unnest(CAST(:people AS uuid[])) p RETURNING sys_id, person"
            ),
            {
                "people": found,
                "kind": kind,
                "title": title.strip(),
                "message": message,
                "link": target,
                "severity": severity,
            },
        )
    )
    await _queue_deliveries(conn, kind, [r.sys_id for r in made], [r.person for r in made])
    return len(found)


async def _queue_deliveries(
    conn: AsyncConnection, kind: str, notifications: list[uuid.UUID], people: list[uuid.UUID]
) -> None:
    """A delivery per linked channel the person gets this kind on: their choice, else the
    kind's channels by default."""
    await conn.execute(
        text(
            """
INSERT INTO sys.notification_delivery (notification, person, channel, state, attempts, not_before)
SELECT n.id, n.person, pc.channel, 'pending', 0, now()
FROM unnest(CAST(:ids AS uuid[]), CAST(:people AS uuid[])) AS n(id, person)
JOIN sys.person_channel pc ON pc.person = n.person AND pc.state = 'linked'
    AND pc.deleted IS NULL
LEFT JOIN sys.notification_subscription s ON s.person = n.person AND s.kind = :kind
    AND s.channel = pc.channel AND s.deleted IS NULL
WHERE COALESCE(s."on", EXISTS (
    SELECT 1 FROM sys.notification_kind k WHERE k.name = :kind AND k.deleted IS NULL
    AND k.channels ? pc.channel))
"""
        ),
        {"ids": notifications, "people": people, "kind": kind},
    )


async def clean_up(conn: AsyncConnection, now: datetime | None = None) -> int:
    """Puts away read notifications after 90 days and unread ones after a year."""
    now = now or datetime.now(UTC)
    await switch_role(conn, "sys")
    done = await conn.execute(
        text(
            "UPDATE sys.notification SET deleted = now() WHERE deleted IS NULL"
            " AND created < CASE WHEN read THEN CAST(:read AS timestamptz)"
            " ELSE CAST(:unread AS timestamptz) END"
        ),
        {"read": now - KEEP_READ, "unread": now - KEEP_UNREAD},
    )
    return done.rowcount
