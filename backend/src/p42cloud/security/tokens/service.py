"""Making and checking API tokens.

A token acts as one person or service account, with at most their rights, and only the roles it
names (and those they include), if it names any. It's shown once; only its hash is stored. Every
token expires: after 90 days unless told otherwise, after a year at the most. A token gives no
second factor, so it can't change access.

People make tokens for themselves; administrators make them for service accounts. Either needs
the second factor given recently.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.kernel.context import Access, current
from p42cloud.kernel.db import switch_role, transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.guards import RECENT, holds_admin
from p42cloud.security.access.service import access_for
from p42cloud.security.sign_in.events import event, failures

PREFIX = "p42_"
DEFAULT_LIFE = timedelta(days=90)
LONGEST_LIFE = timedelta(days=365)
TOUCH_EVERY = timedelta(minutes=5)
WINDOW, ADDRESS_FAILURES = timedelta(minutes=15), 20


@dataclass(frozen=True)
class NewToken:
    sys_id: UUID
    token: str  # shown once
    expires: datetime


@dataclass(frozen=True)
class TokenUse:
    """Who a request with this token acts as, and with what."""

    person: UUID
    name: str
    access: Access

    @property
    def channel(self) -> str:
        return f"api:token:{self.name}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _refuse(code: str, title: str, status: int = 403) -> Problem:
    return Problem(status, code, title)


async def _may_make(conn: AsyncConnection, person: UUID) -> None:
    """The person themselves, or an administrator for a service account; recently verified."""
    context = current()
    if context.access.unrestricted:
        return
    if context.verified_at is None or datetime.now(UTC) - context.verified_at > RECENT:
        raise _refuse("verify_again", "Enter a code from your authenticator app again first.")
    if person == context.person_id:
        return
    kind = await conn.scalar(text("SELECT kind FROM sys.person WHERE sys_id = :p"), {"p": person})
    if kind != "service":
        raise _refuse("not_yours", "People make API tokens for themselves only.")
    if not await holds_admin(conn, context.access.groups):
        raise _refuse("admins_only", "Only administrators make API tokens for service accounts.")


async def make_token(
    conn: AsyncConnection,
    person: UUID,
    name: str,
    *,
    roles: list[str] | None = None,
    expires: datetime | None = None,
) -> NewToken:
    """A new token acting as `person`; returns it once."""
    await switch_role(conn, "sys")
    await _may_make(conn, person)
    now = datetime.now(UTC)
    expires = expires or now + DEFAULT_LIFE
    errors: list[FieldError] = []
    if not now < expires <= now + LONGEST_LIFE:
        errors.append(FieldError("expires", "out_of_range", "Within the next 365 days."))
    if roles is not None:
        known = set(
            (
                await conn.execute(
                    text("SELECT name FROM sys.role WHERE name = ANY(:n) AND deleted IS NULL"),
                    {"n": roles},
                )
            ).scalars()
        )
        errors += [
            FieldError("roles", "unknown_role", f"No role {r!r}.") for r in roles if r not in known
        ]
    if not name.strip():
        errors.append(FieldError("name", "required", "Give the token a name."))
    if errors:
        raise Problem(422, "validation", "Some fields aren't valid.", errors=errors)
    token = PREFIX + secrets.token_urlsafe(32)
    made = await conn.scalar(
        text(
            "INSERT INTO sys.api_token (person, name, token, prefix, roles, expires)"
            " VALUES (:person, :name, :token, :prefix, CAST(:roles AS jsonb), :expires)"
            " RETURNING sys_id"
        ),
        {
            "person": person,
            "name": name.strip()[:80],
            "token": _hash(token),
            "prefix": token[:12],
            "roles": None if roles is None else json.dumps(sorted(roles)),
            "expires": expires,
        },
    )
    if not isinstance(made, UUID):
        raise RuntimeError("no token made")
    await event(conn, "api_token_made", person=str(person), token=str(made))
    return NewToken(made, token, expires)


_FIND = text(
    "SELECT t.sys_id, t.person, t.name, t.roles, t.expires, t.last_used FROM sys.api_token t"
    " JOIN sys.person p ON p.sys_id = t.person"
    " WHERE t.token = :token AND t.deleted IS NULL AND p.deleted IS NULL AND p.active"
)


async def use_token(engine: AsyncEngine, token: str, address: str | None = None) -> TokenUse:
    """Who a request showing this token acts as; 401 if the token isn't (any longer) valid.

    Wrong tokens are security events, and too many from one address are refused for a while.
    """
    now = datetime.now(UTC)
    async with transaction(engine, "sys") as conn:
        throttled = address is not None and (
            await failures(conn, "api_token_refused", "address", address, now - WINDOW)
            >= ADDRESS_FAILURES
        )
        row = None
        if not throttled and token.startswith(PREFIX):
            row = (await conn.execute(_FIND, {"token": _hash(token)})).first()
        if row is None or row.expires <= now:
            await event(conn, "api_token_refused", address=address)
            use = None
        else:
            if row.last_used is None or now - row.last_used >= TOUCH_EVERY:
                await conn.execute(
                    text("UPDATE sys.api_token SET last_used = :now WHERE sys_id = :id"),
                    {"now": now, "id": row.sys_id},
                )
            only = frozenset(row.roles) if row.roles is not None else None
            use = TokenUse(row.person, row.name, await access_for(conn, row.person, only))
    if use is None:
        if throttled:
            raise Problem(429, "slow_down", "Too many attempts. Wait a few minutes.")
        raise Problem(401, "not_signed_in", "The token isn't valid.")
    return use
