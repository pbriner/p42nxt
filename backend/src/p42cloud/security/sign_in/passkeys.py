"""Passkeys and security keys (WebAuthn) as a second factor.

A passkey is bound to the installation's address (`P42_PUBLIC_URL`): the browser signs a fresh
challenge with a key that never leaves the device, and only for this site, so a look-alike site
gets nothing it could use. The installation keeps the public key, the passkey's ID and its
signature counter: a counter going backwards means the key was copied, and is refused.

The cryptographic checks are the `webauthn` library's; challenges come from the session and
each works once.
"""

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import (
    InvalidAuthenticationResponse,
    InvalidCBORData,
    InvalidJSONStructure,
    InvalidRegistrationResponse,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.security.sign_in.credentials import ISSUER

CHALLENGE_BYTES = 32
_REFUSED = (
    InvalidAuthenticationResponse,
    InvalidRegistrationResponse,
    InvalidCBORData,
    InvalidJSONStructure,
    ValueError,
    KeyError,
    TypeError,
)


@dataclass(frozen=True)
class Site:
    """The relying party: the host passkeys are bound to, and the origin browsers report."""

    id: str
    origin: str

    @classmethod
    def of(cls, settings: Settings) -> Site:
        if settings.public_url is None:
            raise Problem(
                409,
                "passkeys_off",
                "Passkeys need the installation's web address; use an authenticator app.",
            )
        address = urlsplit(settings.public_url)
        return cls(str(address.hostname), f"{address.scheme}://{address.netloc}")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text_: str) -> bytes:
    return base64.urlsafe_b64decode(text_ + "=" * (-len(text_) % 4))


def new_challenge() -> tuple[bytes, str]:
    raw = os.urandom(CHALLENGE_BYTES)
    return raw, _b64(raw)


async def _key_ids(conn: AsyncConnection, person: UUID) -> list[str]:
    found = await conn.execute(
        text(
            "SELECT key_id FROM sys.credential WHERE person = :p AND kind = 'passkey'"
            " AND confirmed IS NOT NULL AND deleted IS NULL"
        ),
        {"p": person},
    )
    return [k for k in found.scalars() if k]


async def registration_options(
    conn: AsyncConnection, site: Site, person: UUID, challenge: bytes
) -> dict[str, Any]:
    """What the browser needs to make a passkey for this person (not again for one it has)."""
    await switch_role(conn, "sys")
    row = (
        await conn.execute(
            text("SELECT name, coalesce(email, name) AS account FROM sys.person WHERE sys_id = :p"),
            {"p": person},
        )
    ).one()
    options = generate_registration_options(
        rp_id=site.id,
        rp_name=ISSUER,
        user_id=person.bytes,
        user_name=row.account,
        user_display_name=row.name,
        challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64(k)) for k in await _key_ids(conn, person)
        ],
    )
    return json.loads(options_to_json(options))


async def authentication_options(
    conn: AsyncConnection, site: Site, person: UUID, challenge: bytes
) -> dict[str, Any]:
    """What the browser needs to sign the challenge with one of this person's passkeys."""
    await switch_role(conn, "sys")
    options = generate_authentication_options(
        rp_id=site.id,
        challenge=challenge,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=_unb64(k)) for k in await _key_ids(conn, person)
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return json.loads(options_to_json(options))


async def register(  # noqa: PLR0913 - the answer and everything it's checked against
    conn: AsyncConnection,
    site: Site,
    person: UUID,
    *,
    answer: dict[str, Any],
    challenge: bytes,
    label: str,
    now: datetime,
) -> bool:
    """Stores the new passkey if the browser's answer is right; False if it isn't."""
    try:
        verified = verify_registration_response(
            credential=answer,
            expected_challenge=challenge,
            expected_rp_id=site.id,
            expected_origin=site.origin,
        )
    except _REFUSED:
        return False
    await switch_role(conn, "sys")
    await conn.execute(
        text(
            "INSERT INTO sys.credential"
            " (person, kind, secret, label, key_id, counter, confirmed, last_used)"
            " VALUES (:person, 'passkey', :key, :label, :key_id, :counter, :now, :now)"
        ),
        {
            "person": person,
            "key": _b64(verified.credential_public_key),
            "label": label[:80],
            "key_id": _b64(verified.credential_id),
            "counter": verified.sign_count,
            "now": now,
        },
    )
    return True


async def check(  # noqa: PLR0913 - the answer and everything it's checked against
    conn: AsyncConnection,
    site: Site,
    person: UUID,
    *,
    answer: dict[str, Any],
    challenge: bytes,
    now: datetime,
) -> bool:
    """Whether the answer is the challenge signed by one of the person's passkeys."""
    await switch_role(conn, "sys")
    key_id = answer.get("id") if isinstance(answer, dict) else None  # pyright: ignore[reportUnnecessaryIsInstance]
    row = (
        await conn.execute(
            text(
                "SELECT sys_id, secret, counter FROM sys.credential WHERE person = :p"
                " AND kind = 'passkey' AND key_id = :k AND confirmed IS NOT NULL"
                " AND deleted IS NULL FOR UPDATE"
            ),
            {"p": person, "k": key_id},
        )
    ).first()
    if row is None:
        return False
    try:
        verified = verify_authentication_response(
            credential=answer,
            expected_challenge=challenge,
            expected_rp_id=site.id,
            expected_origin=site.origin,
            credential_public_key=_unb64(row.secret),
            credential_current_sign_count=row.counter or 0,
        )
    except _REFUSED:
        return False
    await conn.execute(
        text("UPDATE sys.credential SET counter = :c, last_used = :now WHERE sys_id = :id"),
        {"c": verified.new_sign_count, "now": now, "id": row.sys_id},
    )
    return True


def challenge_of(stored: str | None) -> bytes:
    if not stored:
        raise Problem(409, "no_challenge", "Ask for a new passkey challenge first.")
    return _unb64(stored)
