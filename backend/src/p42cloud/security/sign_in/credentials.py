"""A person's credentials: their password, authenticator apps and recovery codes.

These functions do what they're asked: who may ask (the person, an administrator) is for the
caller to check. Changing a password or adding a second factor ends the person's other sessions.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.sign_in import passwords, totp
from p42cloud.security.sign_in.events import event

ISSUER = "P42cloud"
PURPOSE = "second factor"
RECOVERY_CODES = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I: read and typed without doubt


@dataclass(frozen=True)
class NewKey:
    credential: UUID
    key: str  # to type into the app
    link: str  # otpauth://, for a QR code


def _recovery_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


async def end_sessions(conn: AsyncConnection, person: UUID, keep: UUID | None = None) -> None:
    await switch_role(conn, "sys")
    await conn.execute(
        text(
            "UPDATE sys.session SET deleted = now() WHERE person = :person AND deleted IS NULL"
            " AND sys_id IS DISTINCT FROM :keep"
        ),
        {"person": person, "keep": keep},
    )


async def store_password_hash(conn: AsyncConnection, person: UUID, password: str) -> None:
    await switch_role(conn, "sys")
    hashed = passwords.hash_password(password)
    changed = await conn.execute(
        text(
            "UPDATE sys.credential SET secret = :hash WHERE person = :person"
            " AND kind = 'password' AND deleted IS NULL"
        ),
        {"person": person, "hash": hashed},
    )
    if changed.rowcount == 0:
        await conn.execute(
            text(
                "INSERT INTO sys.credential (person, kind, secret)"
                " VALUES (:person, 'password', :hash)"
            ),
            {"person": person, "hash": hashed},
        )


async def set_password(
    conn: AsyncConnection, person: UUID, password: str, keep: UUID | None = None
) -> None:
    """A new password, if it's good enough; the person's other sessions end."""
    await switch_role(conn, "sys")
    email = await conn.scalar(text("SELECT email FROM sys.person WHERE sys_id = :p"), {"p": person})
    passwords.check(password, email)
    await store_password_hash(conn, person, password)
    await end_sessions(conn, person, keep)
    await event(conn, "password_changed", person=str(person))


async def has_second_factor(conn: AsyncConnection, person: UUID) -> bool:
    await switch_role(conn, "sys")
    return bool(
        await conn.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM sys.credential WHERE person = :p AND deleted IS NULL"
                " AND (kind = 'recovery'"
                " OR (kind IN ('totp', 'passkey') AND confirmed IS NOT NULL)))"
            ),
            {"p": person},
        )
    )


async def start_totp(conn: AsyncConnection, sealer: Sealer, person: UUID, label: str) -> NewKey:
    """A new authenticator app: its key, to show once. It counts after its first right code."""
    await switch_role(conn, "sys")
    key = totp.new_key()
    account = await conn.scalar(
        text("SELECT coalesce(email, name) FROM sys.person WHERE sys_id = :p"), {"p": person}
    )
    made = await conn.scalar(
        text(
            "INSERT INTO sys.credential (person, kind, secret, label)"
            " VALUES (:person, 'totp', :secret, :label) RETURNING sys_id"
        ),
        {"person": person, "secret": sealer.seal(key, PURPOSE), "label": label[:80]},
    )
    if not isinstance(made, UUID):
        raise RuntimeError("no credential made")
    return NewKey(made, totp.readable(key), totp.link(key, ISSUER, str(account)))


async def confirm_totp(  # noqa: PLR0913 - the clock is passed in for tests
    conn: AsyncConnection,
    sealer: Sealer,
    person: UUID,
    credential: UUID,
    code: str,
    *,
    now: datetime | None = None,
) -> list[str]:
    """The app's first right code makes it count. Returns new recovery codes if there were none."""
    await switch_role(conn, "sys")
    now = now or datetime.now(UTC)
    row = (
        await conn.execute(
            text(
                "SELECT secret FROM sys.credential WHERE sys_id = :id AND person = :person"
                " AND kind = 'totp' AND confirmed IS NULL AND deleted IS NULL"
            ),
            {"id": credential, "person": person},
        )
    ).first()
    step = None
    if row is not None:
        step = totp.accepted_step(sealer.unseal(row.secret, PURPOSE), code, now, None)
    if step is None:
        raise Problem(422, "wrong_code", "That code isn't right. Check the app and try again.")
    await conn.execute(
        text(
            "UPDATE sys.credential SET confirmed = :now, last_step = :step, last_used = :now"
            " WHERE sys_id = :id"
        ),
        {"now": now, "step": step, "id": credential},
    )
    await event(conn, "second_factor_added", person=str(person))
    return await recovery_codes_if_none(conn, person)


async def recovery_codes_if_none(conn: AsyncConnection, person: UUID) -> list[str]:
    """New recovery codes with the first second factor; none if the person has some."""
    await switch_role(conn, "sys")
    has_codes = await conn.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM sys.credential WHERE person = :p"
            " AND kind = 'recovery' AND deleted IS NULL)"
        ),
        {"p": person},
    )
    return [] if has_codes else await new_recovery_codes(conn, person)


async def new_recovery_codes(conn: AsyncConnection, person: UUID) -> list[str]:
    """Ten new one-time codes, to show once; the old ones stop working."""
    await switch_role(conn, "sys")
    await conn.execute(
        text(
            "UPDATE sys.credential SET deleted = now() WHERE person = :p AND kind = 'recovery'"
            " AND deleted IS NULL"
        ),
        {"p": person},
    )
    codes: list[str] = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
        code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
        codes.append(code)
        await conn.execute(
            text(
                "INSERT INTO sys.credential (person, kind, secret) VALUES (:p, 'recovery', :hash)"
            ),
            {"p": person, "hash": _recovery_hash(code)},
        )
    await event(conn, "recovery_codes_made", person=str(person))
    return codes


async def second_factor(
    conn: AsyncConnection, sealer: Sealer, person: UUID, code: str, now: datetime
) -> str | None:
    """Checks an authenticator code or a recovery code; returns which it was, or None."""
    await switch_role(conn, "sys")
    apps = (
        await conn.execute(
            text(
                "SELECT sys_id, secret, last_step FROM sys.credential WHERE person = :p"
                " AND kind = 'totp' AND confirmed IS NOT NULL AND deleted IS NULL"
                " FOR UPDATE"
            ),
            {"p": person},
        )
    ).all()
    for app in apps:
        step = totp.accepted_step(sealer.unseal(app.secret, PURPOSE), code, now, app.last_step)
        if step is not None:
            await conn.execute(
                text(
                    "UPDATE sys.credential SET last_step = :step, last_used = :now"
                    " WHERE sys_id = :id"
                ),
                {"step": step, "now": now, "id": app.sys_id},
            )
            return "totp"
    used = await conn.scalar(
        text(
            "UPDATE sys.credential SET deleted = now(), last_used = :now WHERE sys_id = ("
            " SELECT sys_id FROM sys.credential WHERE person = :p AND kind = 'recovery'"
            " AND deleted IS NULL AND secret = :hash LIMIT 1) RETURNING sys_id"
        ),
        {"p": person, "hash": _recovery_hash(code), "now": now},
    )
    if used is None:
        return None
    await event(conn, "recovery_code_used", person=str(person))
    return "recovery"
