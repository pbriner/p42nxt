"""Who is asking: a session cookie (the browser) or an API token (scripts, integrations).

The browser's session cookie is HttpOnly; changes made with it also need the CSRF header (the
value of the readable CSRF cookie, which another site can't read) and, when it's sent, an
Origin of this installation. Tokens need neither: they're never sent by a browser on its own.
Every caller is held to a rate: too many requests in a minute are refused for a while.
"""

import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Request

from p42cloud.data import settings
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Access, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.service import access_for
from p42cloud.web.services import Services

SESSION_COOKIE = "p42_session"
CSRF_COOKIE = "p42_csrf"
CSRF_HEADER = "x-csrf-token"
BACKGROUND_HEADER = "x-p42-background"
SAFE = frozenset({"GET", "HEAD", "OPTIONS"})
_MAX_COUNTERS = 100_000  # past this, counters of earlier minutes are dropped


@dataclass(frozen=True)
class Caller:
    person: UUID
    access: Access
    channel: str
    verified_at: datetime | None
    session: UUID | None  # the session, for the browser; None for a token
    zone: str = "UTC"  # the installation's time zone

    def context(self, language: str) -> Context:
        return Context(
            channel=self.channel,
            person_id=self.person,
            language=language,
            access=self.access,
            verified_at=self.verified_at,
            zone=self.zone,
        )


def address(request: Request) -> str | None:
    return request.client.host if request.client else None


def language(request: Request) -> str:
    """The first language the browser asks for, as a two-letter code; English otherwise."""
    first = request.headers.get("accept-language", "en").split(",")[0].split(";")[0].strip()
    code = first.split("-")[0].lower()
    return code if len(code) == 2 and code.isalpha() else "en"  # noqa: PLR2004


class Rates:
    """Requests per caller and minute, counted in this process."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._counts: dict[tuple[str, int], int] = defaultdict(int)

    def take(self, key: str) -> None:
        minute = int(time.time() // 60)
        if len(self._counts) > _MAX_COUNTERS:
            self._counts = defaultdict(
                int, {k: v for k, v in self._counts.items() if k[1] == minute}
            )
        self._counts[(key, minute)] += 1
        if self._counts[(key, minute)] > self.per_minute:
            raise Problem(
                429,
                "slow_down",
                "Too many requests. Wait a moment and try again.",
                extra={"retry_after": 60 - int(time.time() % 60)},
            )


def check_origin(request: Request, settings: Settings) -> None:
    """A change with cookies must come from this installation's own pages."""
    origin = request.headers.get("origin")
    if request.method in SAFE or origin is None or settings.public_url is None:
        return
    mine = urlsplit(settings.public_url)
    if urlsplit(origin).netloc != mine.netloc:
        raise Problem(403, "wrong_origin", "This request didn't come from this installation.")


async def who(request: Request) -> Caller:
    """The caller, or 401. Used by every route that needs someone signed in."""
    services: Services = request.app.state.services
    services.rates.take(f"address:{address(request)}")
    authorization = request.headers.get("authorization", "")
    with working_as(Context(channel="api")):
        if authorization.lower().startswith("bearer "):
            use = await services.tokens(authorization[7:].strip(), address(request))
            services.rates.take(f"person:{use.person}")
            return Caller(
                use.person, use.access, use.channel, None, None, await services.time_zone()
            )
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise Problem(401, "not_signed_in", "Please sign in.")
        # Requests the app makes by itself say so; they don't keep the session alive.
        background = request.headers.get(BACKGROUND_HEADER) == "1"
        session = await services.sign_in.session(token, active=not background)
        if request.method not in SAFE:
            check_origin(request, services.settings)
            given = request.headers.get(CSRF_HEADER)
            if not given or given != request.cookies.get(CSRF_COOKIE):
                raise Problem(403, "csrf", "Reload the page and try again.")
        services.rates.take(f"person:{session.person}")
        async with transaction(services.engine, "sys") as conn:
            access = await access_for(conn, session.person)
            zone = await settings.value(conn, "sys.time_zone")
    return Caller(session.person, access, "ui", session.verified, session.sys_id, str(zone))
