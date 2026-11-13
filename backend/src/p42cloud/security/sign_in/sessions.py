"""Signing in and sessions: a password, then always a second factor; sessions kept on the server.

1. `start`: email and password. A wrong one, an unknown email or an inactive person all get the
   same answer, after the same time. The session waits for the second factor (or, for someone
   who has none yet, for setting one up).
2. `finish`: an authenticator code, a recovery code, or a passkey's answer to a challenge
   (`passkey_challenge`). The waiting session ends and a new one begins, with a new token.
3. `session`: every request shows its token. Sessions end after a while unused, and after a
   maximum time in any case.

Every failure is a security event, committed even though the attempt is refused, and failures
slow further attempts down: per account (the email given, whether it exists or not) and per
address. Tokens are random; only their hash is stored.
"""

import hashlib
import secrets
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.sign_in import credentials, passkeys, passwords
from p42cloud.security.sign_in.events import event, failures

PENDING = timedelta(minutes=10)
TOUCH_EVERY = timedelta(minutes=5)
RECENT = timedelta(minutes=15)  # adding a second factor needs one given this recently
WINDOW = timedelta(minutes=15)
ACCOUNT_FAILURES, ADDRESS_FAILURES = 5, 20


# A second factor: an authenticator or recovery code, or a passkey's answer (the browser's JSON).
type Proof = str | dict[str, Any]


@dataclass(frozen=True)
class Started:
    token: str
    stage: str  # second_factor, enroll or active
    person: UUID
    expires: datetime


@dataclass(frozen=True)
class Session:
    sys_id: UUID
    person: UUID
    verified: datetime | None  # when the second factor was last given


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _wrong(title: str) -> Problem:
    return Problem(401, "not_signed_in", title)


def _slow_down() -> Problem:
    return Problem(
        429,
        "slow_down",
        "Too many attempts. Wait a few minutes and try again.",
        extra={"retry_after": int(WINDOW.total_seconds())},
    )


_ACCOUNT = text(
    "SELECT p.sys_id, p.email, c.secret FROM sys.person p"
    " LEFT JOIN sys.credential c ON c.person = p.sys_id AND c.kind = 'password'"
    " AND c.deleted IS NULL"
    " WHERE lower(p.email) = :email AND p.deleted IS NULL AND p.active"
    # Service accounts work through API tokens only.
    " AND p.kind <> 'service'"
)
_SESSION = text(
    "SELECT sys_id, person, stage, expires, last_seen, verified, challenge FROM sys.session"
    " WHERE token = :token AND deleted IS NULL"
)


class SignIn:
    def __init__(
        self,
        engine: AsyncEngine,
        sealer: Sealer,
        settings: Settings,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine = engine
        self.sealer = sealer
        self.settings = settings
        self.idle = timedelta(minutes=settings.session_idle_minutes)
        self.longest = timedelta(hours=settings.session_max_hours)
        self.now = now

    @asynccontextmanager
    async def _work(self) -> AsyncGenerator[AsyncConnection]:
        async with transaction(self.engine, "sys") as conn:
            yield conn

    async def _new_session(
        self, conn: AsyncConnection, person: UUID, stage: str, details: dict[str, Any]
    ) -> Started:
        token = secrets.token_urlsafe(32)
        now = self.now()
        expires = now + (self.longest if stage == "active" else PENDING)
        await conn.execute(
            text(
                "INSERT INTO sys.session (person, token, stage, expires, last_seen, verified,"
                " address, agent) VALUES (:person, :token, :stage, :expires, :now, :verified,"
                " :address, :agent)"
            ),
            {
                "person": person,
                "token": _hash(token),
                "stage": stage,
                "expires": expires,
                "now": now,
                "verified": now if stage == "active" else None,
                "address": details.get("address"),
                "agent": (details.get("agent") or "")[:255] or None,
            },
        )
        return Started(token, stage, person, expires)

    async def _pending(self, conn: AsyncConnection, token: str, stage: str) -> Any:
        row = (await conn.execute(_SESSION, {"token": _hash(token)})).first()
        if row is None or row.stage != stage or row.expires <= self.now():
            raise _wrong("Your sign-in has expired. Start again.")
        return row

    async def _proven(self, conn: AsyncConnection, row: Any, proof: Proof) -> str | None:
        """Checks a second factor: a code, or a passkey's answer to the session's challenge."""
        if isinstance(proof, str):
            return await credentials.second_factor(conn, self.sealer, row.person, proof, self.now())
        challenge = passkeys.challenge_of(row.challenge)
        await conn.execute(  # each challenge works once, right or wrong
            text("UPDATE sys.session SET challenge = NULL WHERE sys_id = :id"), {"id": row.sys_id}
        )
        site = passkeys.Site.of(self.settings)
        ok = await passkeys.check(
            conn, site, row.person, answer=proof, challenge=challenge, now=self.now()
        )
        return "passkey" if ok else None

    async def _challenge(self, conn: AsyncConnection, row: Any) -> bytes:
        raw, stored = passkeys.new_challenge()
        await conn.execute(
            text("UPDATE sys.session SET challenge = :c WHERE sys_id = :id"),
            {"c": stored, "id": row.sys_id},
        )
        return raw

    async def _row(self, conn: AsyncConnection, token: str, *stages: str) -> Any:
        """The session in one of these stages; signed-in ones must still be valid."""
        row = (await conn.execute(_SESSION, {"token": _hash(token)})).first()
        if row is None or row.stage not in stages or row.expires <= self.now():
            raise _wrong("Please sign in again.")
        if row.stage == "active":
            await self.session(token)  # not idle too long
        return row

    async def start(
        self, email: str, password: str, address: str | None = None, agent: str | None = None
    ) -> Started:
        """Email and password: the first half of signing in."""
        email = email.strip().lower()
        since = self.now() - WINDOW
        async with self._work() as conn:
            throttled = await failures(
                conn, "sign_in_failed", "email", email, since
            ) >= ACCOUNT_FAILURES or (
                address is not None
                and await failures(conn, "sign_in_failed", "address", address, since)
                >= ADDRESS_FAILURES
            )
            row = None if throttled else (await conn.execute(_ACCOUNT, {"email": email})).first()
            hashed = row.secret if row is not None else None
            if throttled or not passwords.verify(hashed, password) or row is None:
                await event(conn, "sign_in_failed", email=email, address=address)
                started = None
            else:
                if hashed and passwords.needs_rehash(hashed):
                    await credentials.store_password_hash(conn, row.sys_id, password)
                has_second = await credentials.has_second_factor(conn, row.sys_id)
                stage = "second_factor" if has_second else "enroll"
                started = await self._new_session(
                    conn, row.sys_id, stage, {"address": address, "agent": agent}
                )
        if started is None:
            raise _slow_down() if throttled else _wrong("Wrong email or password.")
        return started

    async def finish(
        self, token: str, code: Proof, address: str | None = None, agent: str | None = None
    ) -> Started:
        """The second factor: the waiting session ends, a signed-in one with a new token begins."""
        since = self.now() - WINDOW
        async with self._work() as conn:
            row = await self._pending(conn, token, "second_factor")
            person = str(row.person)
            throttled = await failures(conn, "second_factor_failed", "person", person, since)
            method = None
            if throttled < ACCOUNT_FAILURES:
                method = await self._proven(conn, row, code)
            if method is None:
                await event(conn, "second_factor_failed", person=person, address=address)
                started = None
            else:
                await conn.execute(
                    text("UPDATE sys.session SET deleted = now() WHERE sys_id = :id"),
                    {"id": row.sys_id},
                )
                started = await self._new_session(
                    conn, row.person, "active", {"address": address, "agent": agent}
                )
                await event(conn, "sign_in", person=person, method=method, address=address)
        if started is None:
            if throttled >= ACCOUNT_FAILURES:
                raise _slow_down()
            raise _wrong("That code isn't right.")
        return started

    async def enroll(self, token: str, label: str) -> credentials.NewKey:
        """Someone without a second factor sets one up before they're signed in."""
        async with self._work() as conn:
            row = await self._pending(conn, token, "enroll")
            return await credentials.start_totp(conn, self.sealer, row.person, label)

    async def finish_enrolling(
        self, token: str, key: UUID, code: str, address: str | None = None
    ) -> tuple[Started, list[str]]:
        """The new authenticator's first code: signed in, and the recovery codes, shown once."""
        async with self._work() as conn:
            row = await self._pending(conn, token, "enroll")
            codes = await credentials.confirm_totp(
                conn, self.sealer, row.person, key, code, now=self.now()
            )
            await conn.execute(
                text("UPDATE sys.session SET deleted = now() WHERE sys_id = :id"),
                {"id": row.sys_id},
            )
            started = await self._new_session(conn, row.person, "active", {"address": address})
            await event(conn, "sign_in", person=str(row.person), method="new_totp", address=address)
        return started, codes

    async def session(self, token: str) -> Session:
        """The signed-in session a request's token belongs to; 401 if there's none (any more)."""
        now = self.now()
        async with self._work() as conn:
            row = (await conn.execute(_SESSION, {"token": _hash(token)})).first()
            if row is None or row.stage != "active":
                found = None
            elif row.expires <= now or row.last_seen + self.idle <= now:
                await conn.execute(
                    text("UPDATE sys.session SET deleted = now() WHERE sys_id = :id"),
                    {"id": row.sys_id},
                )
                await event(conn, "session_expired", person=str(row.person))
                found = None
            else:
                if now - row.last_seen >= TOUCH_EVERY:
                    await conn.execute(
                        text("UPDATE sys.session SET last_seen = :now WHERE sys_id = :id"),
                        {"now": now, "id": row.sys_id},
                    )
                found = Session(row.sys_id, row.person, row.verified)
        if found is None:
            raise _wrong("Please sign in.")
        return found

    async def verify_again(self, token: str, code: Proof) -> Session:
        """The second factor again, for changes that need it to be recent (access changes)."""
        current = await self.session(token)
        since = self.now() - WINDOW
        async with self._work() as conn:
            row = await self._row(conn, token, "active")
            person = str(current.person)
            throttled = await failures(conn, "second_factor_failed", "person", person, since)
            method = None
            if throttled < ACCOUNT_FAILURES:
                method = await self._proven(conn, row, code)
            if method is not None:
                await conn.execute(
                    text("UPDATE sys.session SET verified = :now WHERE sys_id = :id"),
                    {"now": self.now(), "id": current.sys_id},
                )
            else:
                await event(conn, "second_factor_failed", person=person)
        if method is None:
            raise (
                _slow_down() if throttled >= ACCOUNT_FAILURES else _wrong("That code isn't right.")
            )
        return Session(current.sys_id, current.person, self.now())

    async def passkey_challenge(self, token: str) -> dict[str, Any]:
        """A challenge for one of the person's passkeys: to finish signing in, or verify again."""
        site = passkeys.Site.of(self.settings)
        async with self._work() as conn:
            row = await self._row(conn, token, "second_factor", "active")
            challenge = await self._challenge(conn, row)
            return await passkeys.authentication_options(conn, site, row.person, challenge)

    async def passkey_registration(self, token: str) -> dict[str, Any]:
        """What the browser needs to make a new passkey: signed in, or setting up a first one."""
        site = passkeys.Site.of(self.settings)
        async with self._work() as conn:
            row = await self._row(conn, token, "enroll", "active")
            challenge = await self._challenge(conn, row)
            return await passkeys.registration_options(conn, site, row.person, challenge)

    async def _register(
        self, conn: AsyncConnection, row: Any, answer: dict[str, Any], label: str
    ) -> list[str]:
        challenge = passkeys.challenge_of(row.challenge)
        await conn.execute(
            text("UPDATE sys.session SET challenge = NULL WHERE sys_id = :id"), {"id": row.sys_id}
        )
        site = passkeys.Site.of(self.settings)
        made = await passkeys.register(
            conn, site, row.person, answer=answer, challenge=challenge, label=label, now=self.now()
        )
        if not made:
            raise Problem(422, "wrong_passkey", "The passkey couldn't be added. Try again.")
        await event(conn, "second_factor_added", person=str(row.person), kind="passkey")
        return await credentials.recovery_codes_if_none(conn, row.person)

    async def _recently_verified(self, token: str) -> Session:
        current = await self.session(token)
        if current.verified is None or self.now() - current.verified > RECENT:
            raise Problem(
                403, "verify_again", "Enter a code or use a passkey again to add a second factor."
            )
        return current

    async def add_app(self, token: str, label: str) -> credentials.NewKey:
        """A new authenticator app while signed in (needs the second factor given recently)."""
        current = await self._recently_verified(token)
        async with self._work() as conn:
            return await credentials.start_totp(conn, self.sealer, current.person, label)

    async def confirm_app(self, token: str, key: UUID, code: str) -> list[str]:
        """The new app's first code; the person's other sessions end."""
        current = await self._recently_verified(token)
        async with self._work() as conn:
            codes = await credentials.confirm_totp(
                conn, self.sealer, current.person, key, code, now=self.now()
            )
            await credentials.end_sessions(conn, current.person, keep=current.sys_id)
        return codes

    async def add_passkey(self, token: str, answer: dict[str, Any], label: str) -> list[str]:
        """A new passkey while signed in; the person's other sessions end. Returns recovery codes
        if they had none.

        Needs the second factor given recently: whoever took over an open session can't add a
        key of their own and keep the access.
        """
        await self._recently_verified(token)
        async with self._work() as conn:
            row = await self._row(conn, token, "active")
            codes = await self._register(conn, row, answer, label)
            await credentials.end_sessions(conn, row.person, keep=row.sys_id)
        return codes

    async def enroll_with_passkey(
        self, token: str, answer: dict[str, Any], label: str, address: str | None = None
    ) -> tuple[Started, list[str]]:
        """A first second factor that's a passkey: signed in, and the recovery codes, shown once."""
        async with self._work() as conn:
            row = await self._row(conn, token, "enroll")
            codes = await self._register(conn, row, answer, label)
            await conn.execute(
                text("UPDATE sys.session SET deleted = now() WHERE sys_id = :id"),
                {"id": row.sys_id},
            )
            started = await self._new_session(conn, row.person, "active", {"address": address})
            await event(
                conn, "sign_in", person=str(row.person), method="new_passkey", address=address
            )
        return started, codes

    async def sign_out(self, token: str) -> None:
        async with self._work() as conn:
            row = (await conn.execute(_SESSION, {"token": _hash(token)})).first()
            if row is not None:
                await conn.execute(
                    text("UPDATE sys.session SET deleted = now() WHERE sys_id = :id"),
                    {"id": row.sys_id},
                )
                await event(conn, "sign_out", person=str(row.person))
