"""Signing in through a provider (OpenID Connect, authorization code with PKCE).

1. `begin`: the browser is sent to the provider, with a `state` and a `nonce` that work once and
   for 10 minutes, and a PKCE challenge.
2. `complete`: the provider sends the browser back with a code. The code is exchanged for an ID
   token, which is checked: signed by one of the provider's published keys (RS256 or ES256
   only), issued by it, for us, not expired, with our nonce.
3. The person: found by the provider's subject ID (an external ID). Otherwise linked once by email,
   only if the provider confirms the email (or vouches for all its emails), and only a person of
   the kind the provider signs in. Otherwise made, if the provider may make people. The fields
   mapped from claims are updated at every sign-in, so groups with rules stay right.

The provider's own second factor counts: no second factor is asked. Every failure is a security
event with its reason; the browser only learns that the sign-in didn't work.
"""

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data import installation
from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import EVERYTHING, Context, current, working_as
from p42cloud.kernel.db import switch_role, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.groups import sync_from_provider
from p42cloud.security.sign_in.events import event
from p42cloud.security.sign_in.sessions import SignIn, Started

CALLBACK = "/api/v1/sign-in/provider-callback"
FLOW_LIFE = timedelta(minutes=10)
ALGORITHMS = ["RS256", "ES256"]  # never "none", never a shared-secret algorithm
LEEWAY = 60
DISCOVERY_LIFE = 3600
PURPOSE = "provider secret"


@dataclass(frozen=True)
class Redirect:
    url: str  # where to send the browser


class Refused(Exception):
    """Why a provider sign-in didn't work: for the security event, never for the browser."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def set_client_secret(
    conn: AsyncConnection, sealer: Sealer, provider: UUID, secret: str
) -> None:
    """The provider's client secret, sealed with the master key. Only administrators call this."""
    await switch_role(conn, "sys")
    await conn.execute(
        text("UPDATE sys.identity_provider SET client_secret = :s WHERE sys_id = :id"),
        {"s": sealer.seal(secret.encode(), PURPOSE), "id": provider},
    )


_PROVIDER = text(
    "SELECT sys_id, name, issuer, client_id, client_secret, scopes, kind, create_people,"
    " trust_email, claims, groups_claim FROM sys.identity_provider"
    " WHERE sys_id = :id AND active AND deleted IS NULL"
)


class Providers:
    def __init__(  # noqa: PLR0913 - what talking to providers needs, passed in
        self,
        engine: AsyncEngine,
        sealer: Sealer,
        *,
        settings: Settings,
        http: httpx.AsyncClient,
        sign_in: SignIn,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine = engine
        self.sealer = sealer
        self.settings = settings
        self.http = http
        self.sign_in = sign_in
        self.now = now
        self._discovered: dict[str, tuple[float, dict[str, Any], dict[str, Any]]] = {}

    @property
    def redirect_uri(self) -> str:
        """Where providers send people back: needs the app's address (a setting)."""
        public_url = installation.public_url(self.settings)
        if public_url is None:
            raise Problem(
                409, "providers_off", "Sign-in providers need the app's address (Settings)."
            )
        return public_url.rstrip("/") + CALLBACK

    async def available(self) -> list[tuple[UUID, str]]:
        """The providers to offer on the sign-in page."""
        async with transaction(self.engine, "sys") as conn:
            rows = await conn.execute(
                text(
                    "SELECT sys_id, name FROM sys.identity_provider"
                    " WHERE active AND deleted IS NULL ORDER BY name"
                )
            )
            return [(r.sys_id, r.name) for r in rows]

    async def _discover(
        self, issuer: str, *, fresh: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The provider's configuration and published keys, kept for an hour."""
        cached = self._discovered.get(issuer)
        if cached is not None and not fresh and time.monotonic() - cached[0] < DISCOVERY_LIFE:
            return cached[1], cached[2]
        found = await self.http.get(issuer.rstrip("/") + "/.well-known/openid-configuration")
        found.raise_for_status()
        config = cast(dict[str, Any], found.json())
        if config.get("issuer") != issuer:
            raise Refused("the configuration names another issuer")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            address = urlsplit(str(config.get(name, "")))
            if address.scheme != "https" and address.hostname != "localhost":
                raise Refused(f"{name} must use https")
        keys = await self.http.get(config["jwks_uri"])
        keys.raise_for_status()
        published = cast(dict[str, Any], keys.json())
        self._discovered[issuer] = (time.monotonic(), config, published)
        return config, published

    async def begin(self, provider: UUID) -> Redirect:
        """Where to send the browser to sign in with this provider."""
        async with transaction(self.engine, "sys") as conn:
            row = (await conn.execute(_PROVIDER, {"id": provider})).first()
            if row is None:
                raise Problem(404, "not_found", "There's no such sign-in provider.")
            try:
                config, _ = await self._discover(row.issuer)
            except (httpx.HTTPError, Refused, KeyError, ValueError) as error:
                raise Problem(
                    502, "provider_unreachable", f"{row.name} can't be reached. Try again later."
                ) from error
            state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            verifier = secrets.token_urlsafe(64)
            await conn.execute(
                text(
                    "INSERT INTO sys.sign_in_flow (provider, state, nonce, verifier, expires)"
                    " VALUES (:provider, :state, :nonce, :verifier, :expires)"
                ),
                {
                    "provider": provider,
                    "state": _hash(state),
                    "nonce": nonce,
                    "verifier": verifier,
                    "expires": self.now() + FLOW_LIFE,
                },
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": row.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": row.scopes or "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return Redirect(f"{config['authorization_endpoint']}?{query}")

    async def complete(
        self, state: str, code: str, address: str | None = None, agent: str | None = None
    ) -> Started:
        """The provider sent the browser back: check everything, then sign the person in."""
        provider_name = "unknown"
        try:
            async with transaction(self.engine, "sys") as conn:
                flow = (
                    await conn.execute(
                        text(
                            "UPDATE sys.sign_in_flow SET deleted = now() WHERE state = :s"
                            " AND deleted IS NULL RETURNING provider, nonce, verifier, expires"
                        ),
                        {"s": _hash(state)},
                    )
                ).first()  # used up, right or wrong
                provider = (
                    (await conn.execute(_PROVIDER, {"id": flow.provider})).first() if flow else None
                )
            if flow is None or provider is None:
                raise Refused("unknown or used state")
            provider_name = provider.name
            if flow.expires <= self.now():
                raise Refused("the sign-in took too long")
            claims = await self._id_token(provider, code, flow.verifier, flow.nonce)
            person = await self._person(provider, claims)
        except (Refused, Problem, httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError) as error:
            async with transaction(self.engine, "sys") as conn:
                await event(
                    conn,
                    "provider_sign_in_failed",
                    provider=provider_name,
                    reason=str(error)[:200] or type(error).__name__,
                    address=address,
                )
            raise Problem(401, "not_signed_in", "The sign-in didn't work. Try again.") from error
        return await self.sign_in.through_provider(person, provider_name, address, agent)

    async def _id_token(
        self, provider: Any, code: str, verifier: str, nonce: str
    ) -> dict[str, Any]:
        config, published = await self._discover(provider.issuer)
        secret = (
            self.sealer.unseal(provider.client_secret, PURPOSE).decode()
            if provider.client_secret
            else None
        )
        answer = await self.http.post(
            config["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": provider.client_id,
                "code_verifier": verifier,
                **({"client_secret": secret} if secret else {}),
            },
        )
        answer.raise_for_status()
        token = cast(str, answer.json()["id_token"])
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in ALGORITHMS:
            raise Refused(f"algorithm {header.get('alg')!r} isn't accepted")
        key = self._key(published, header.get("kid"))
        if key is None:  # the provider may have rotated its keys
            _, published = await self._discover(provider.issuer, fresh=True)
            key = self._key(published, header.get("kid"))
        if key is None:
            raise Refused("signed with an unknown key")
        claims = jwt.decode(
            token,
            key=key,
            algorithms=ALGORITHMS,
            audience=provider.client_id,
            issuer=provider.issuer,
            leeway=LEEWAY,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise Refused("wrong nonce")
        audience: Any = claims["aud"]
        audiences = cast(list[Any], audience) if isinstance(audience, list) else [audience]
        if len(audiences) > 1 and claims.get("azp") != provider.client_id:
            raise Refused("issued for another client")
        return claims

    @staticmethod
    def _key(published: dict[str, Any], kid: str | None) -> Any:
        for found in jwt.PyJWKSet.from_dict(published).keys:
            if kid is None or found.key_id == kid:
                return found.key
        return None

    async def _person(self, provider: Any, claims: dict[str, Any]) -> UUID:
        system = f"provider:{provider.sys_id}"
        email = claims.get("email")
        email = email.strip().lower() if isinstance(email, str) else None
        confirmed = email is not None and (
            claims.get("email_verified") is True or provider.trust_email
        )
        with working_as(
            Context(
                channel=f"provider:{provider.name}",
                request_id=current().request_id,
                access=EVERYTHING,
            )
        ):
            async with transaction(self.engine, "sys") as conn:
                records = Records(conn, await load_catalog(conn))
                person = await self._find(conn, system, str(claims["sub"]))
                if person is None and confirmed:
                    person = await self._link(conn, provider, system, str(claims["sub"]), email)
                if person is None and provider.create_people:
                    made = await records.create(
                        "sys.person",
                        {
                            "name": str(claims.get("name") or email or claims["sub"])[:120],
                            "email": email if confirmed else None,
                            "kind": provider.kind,
                        },
                    )
                    person = made.sys_id
                    await self._remember(conn, system, str(claims["sub"]), person)
                if person is None:
                    raise Refused("no matching person, and the provider doesn't make people")
                await self._check_person(conn, provider, person)
                await self._update(records, provider, person, claims)
                if provider.groups_claim:
                    found = claims.get(provider.groups_claim, [])
                    names = {found} if isinstance(found, str) else set(cast(list[Any], found))
                    await switch_role(conn, "sys")
                    await sync_from_provider(conn, provider.sys_id, person, {str(n) for n in names})
        return person

    async def _find(self, conn: AsyncConnection, system: str, subject: str) -> UUID | None:
        await switch_role(conn, "sys")
        return await conn.scalar(
            text(
                'SELECT e.record FROM sys.external_id e JOIN sys."table" t ON t.sys_id = e."table"'
                " WHERE e.system = :system AND e.external = :subject AND e.deleted IS NULL"
                " AND t.\"schema\" = 'sys' AND t.name = 'person'"
            ),
            {"system": system, "subject": subject},
        )

    async def _remember(
        self, conn: AsyncConnection, system: str, subject: str, person: UUID
    ) -> None:
        await switch_role(conn, "sys")
        await conn.execute(
            text(
                'INSERT INTO sys.external_id (system, external, "table", record)'
                ' SELECT :system, :subject, sys_id, :person FROM sys."table"'
                " WHERE \"schema\" = 'sys' AND name = 'person'"
            ),
            {"system": system, "subject": subject, "person": person},
        )

    async def _link(
        self, conn: AsyncConnection, provider: Any, system: str, subject: str, email: str | None
    ) -> UUID | None:
        """An existing person with this confirmed email, linked to the provider once."""
        await switch_role(conn, "sys")
        person = await conn.scalar(
            text(
                "SELECT p.sys_id FROM sys.person p WHERE p.email = :email AND p.deleted IS NULL"
                " AND p.kind = :kind AND NOT EXISTS (SELECT 1 FROM sys.external_id e"
                " WHERE e.system = :system AND e.record = p.sys_id AND e.deleted IS NULL)"
            ),
            {"email": email, "kind": provider.kind, "system": system},
        )
        if person is not None:
            await self._remember(conn, system, subject, person)
        return person

    async def _check_person(self, conn: AsyncConnection, provider: Any, person: UUID) -> None:
        await switch_role(conn, "sys")
        row = (
            await conn.execute(
                text("SELECT kind, active FROM sys.person WHERE sys_id = :p AND deleted IS NULL"),
                {"p": person},
            )
        ).first()
        if row is None or not row.active:
            raise Refused("the person is inactive")
        if row.kind != provider.kind:
            raise Refused(f"the provider signs in {provider.kind} people, not {row.kind}")

    async def _update(
        self, records: Records, provider: Any, person: UUID, claims: dict[str, Any]
    ) -> None:
        """The fields mapped from claims, as the provider says now (groups with rules follow)."""
        mapping = cast(dict[str, str], provider.claims or {})
        values = {field: claims[claim] for field, claim in mapping.items() if claim in claims}
        if not values:
            return
        current_record = await records.read("sys.person", person)
        changed = {k: v for k, v in values.items() if current_record.values.get(k) != v}
        if changed:
            await records.update("sys.person", person, changed, current_record.etag)
