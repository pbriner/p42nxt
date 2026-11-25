"""For the operator on the server, when nobody can sign in any more: a person's second factor
reset (they set up a new one at their next sign-in, with their password), and a short-lived API
token for a person. Both are security events, made at the command line only: whoever runs them
already controls the server.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.security.sign_in.credentials import end_sessions
from p42cloud.security.sign_in.events import event
from p42cloud.security.tokens.service import NewToken, make_token

LONGEST_TOKEN = timedelta(hours=24)
SECOND_FACTORS = ("totp", "passkey", "recovery")


async def _person(conn: AsyncConnection, email: str) -> UUID:
    await switch_role(conn, "sys")
    found = await conn.scalar(
        text(
            "SELECT sys_id FROM sys.person WHERE lower(email) = lower(:e) AND deleted IS NULL"
            " AND active"
        ),
        {"e": email.strip()},
    )
    if not isinstance(found, UUID):
        raise Problem(404, "not_found", f"No active person with the email {email}.")
    return found


async def reset_second_factor(conn: AsyncConnection, email: str) -> UUID:
    """Their authenticator apps, passkeys and recovery codes retired, their sessions ended."""
    person = await _person(conn, email)
    retired = await conn.execute(
        text(
            "UPDATE sys.credential SET deleted = now() WHERE person = :p AND deleted IS NULL"
            " AND kind = ANY(:kinds)"
        ),
        {"p": person, "kinds": list(SECOND_FACTORS)},
    )
    await end_sessions(conn, person)
    await event(conn, "second_factor_reset", person=str(person), credentials=str(retired.rowcount))
    return person


async def operator_token(conn: AsyncConnection, email: str, name: str, hours: int) -> NewToken:
    """A token acting as the person, for at most a day."""
    if not 1 <= hours <= LONGEST_TOKEN.total_seconds() // 3600:
        raise Problem(422, "validation", "A token from the command line lasts 1 to 24 hours.")
    person = await _person(conn, email)
    made = await make_token(conn, person, name, expires=datetime.now(UTC) + timedelta(hours=hours))
    await event(conn, "operator_token_made", person=str(person), token=str(made.sys_id))
    return made
