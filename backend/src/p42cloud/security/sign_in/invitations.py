"""Invitations and resets: a one-time link, valid 7 days, that lets a person set a password and
then a second factor.

Only administrators make them, with a second factor given recently. A reset first ends the
person's sessions and retires all their credentials (password, apps, passkeys, recovery codes).
The administrator passes the link on; they never choose anyone's password. Only the link's hash
is stored.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.declarations import Table
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.guards import RECENT, holds_admin
from p42cloud.security.sign_in.credentials import end_sessions
from p42cloud.security.sign_in.events import event

LIFE = timedelta(days=7)

INVITATION = Table(
    "invitation",
    "Invitation",
    {
        "person": Reference("Person", "sys.person", required=True),
        "token": String("Link hash", max_length=64, required=True, unique=True),
        "kind": Choice("Kind", {"invitation": "Invitation", "reset": "Reset"}, required=True),
        "expires": DateTime("Expires", required=True),
        "used": DateTime("Used"),
    },
    label_plural="Invitations",
)


@dataclass(frozen=True)
class NewInvitation:
    token: str  # shown once, in the link
    expires: datetime


def hashed(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def invite(conn: AsyncConnection, person: UUID, *, reset: bool = False) -> NewInvitation:
    """A link for the person to set a password and a second factor (administrators only)."""
    await switch_role(conn, "sys")
    context = current()
    if not context.access.unrestricted:
        if context.verified_at is None or datetime.now(UTC) - context.verified_at > RECENT:
            raise Problem(403, "verify_again", "Enter a code from your authenticator app first.")
        if not await holds_admin(conn, context.access.groups):
            raise Problem(403, "admins_only", "Only administrators invite people.")
    row = (
        await conn.execute(
            text("SELECT kind, active FROM sys.person WHERE sys_id = :p AND deleted IS NULL"),
            {"p": person},
        )
    ).first()
    if row is None or not row.active:
        raise Problem(404, "not_found", "There's no such active person.")
    if row.kind == "service":
        raise Problem(409, "service_account", "Service accounts use API tokens, not invitations.")
    if reset:
        await conn.execute(
            text("UPDATE sys.credential SET deleted = now() WHERE person = :p AND deleted IS NULL"),
            {"p": person},
        )
        await end_sessions(conn, person)
        await event(conn, "credentials_reset", person=str(person))
    # Only the newest link works.
    await conn.execute(
        text(
            "UPDATE sys.invitation SET deleted = now() WHERE person = :p AND deleted IS NULL"
            " AND used IS NULL"
        ),
        {"p": person},
    )
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + LIFE
    await conn.execute(
        text("INSERT INTO sys.invitation (person, token, kind, expires) VALUES (:p, :t, :k, :e)"),
        {"p": person, "t": hashed(token), "k": "reset" if reset else "invitation", "e": expires},
    )
    await event(conn, "invitation_made", person=str(person), kind="reset" if reset else "new")
    return NewInvitation(token, expires)
