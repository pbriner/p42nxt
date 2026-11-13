"""Authenticator app codes (TOTP, RFC 6238): six digits, a new one every 30 seconds.

A code is accepted for the current step and one step either side (clocks drift), and each step
only once: a code seen by someone else can't be used again.
"""

import base64
import hashlib
import hmac
import os
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
_DRIFT = 1


def new_key() -> bytes:
    return os.urandom(20)


def readable(key: bytes) -> str:
    """The key as people type it into an app (base32, no padding)."""
    return base64.b32encode(key).decode().rstrip("=")


def link(key: bytes, issuer: str, account: str) -> str:
    """The otpauth:// link an app reads from a QR code."""
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": readable(key), "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"


def step_of(moment: datetime) -> int:
    return int(moment.timestamp()) // STEP_SECONDS


def code(key: bytes, step: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def accepted_step(key: bytes, given: str, moment: datetime, last_step: int | None) -> int | None:
    """The step the code belongs to, if it's right and not used before; None otherwise."""
    given = given.strip().replace(" ", "")
    if len(given) != DIGITS or not given.isdigit():
        return None
    now = step_of(moment)
    for step in range(now - _DRIFT, now + _DRIFT + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code(key, step), given):
            return step
    return None
