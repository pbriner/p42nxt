"""Passwords: what's accepted, and hashing them (Argon2id).

A password needs 12 characters or more, and must not be one of the breached passwords that ship
with each release (checked offline: nothing leaves the installation), nor contain the person's
email name. At most 256 characters, so hashing stays cheap for the server.
"""

import gzip
from functools import cache
from importlib import resources

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from p42cloud.kernel.errors import FieldError, Problem

MIN_LENGTH, MAX_LENGTH = 12, 256
_HASHER = PasswordHasher()  # Argon2id, RFC 9106's recommended low-memory settings
# Checked when no account matches, so a wrong email takes as long as a wrong password.
_DUMMY = _HASHER.hash("not a real password, only for timing")


@cache
def _breached() -> frozenset[str]:
    packed = resources.files(__package__).joinpath("breached.txt.gz").read_bytes()
    return frozenset(gzip.decompress(packed).decode().split("\n")) - {""}


def problems(password: str, email: str | None = None) -> list[str]:
    found: list[str] = []
    if len(password) < MIN_LENGTH:
        found.append(f"Use at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        found.append(f"Use at most {MAX_LENGTH} characters.")
    if password.lower() in _breached():
        found.append("This password is known from data breaches; choose another.")
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in password.lower():  # noqa: PLR2004
        found.append("Don't use your email name in your password.")
    return found


def check(password: str, email: str | None = None) -> None:
    found = problems(password, email)
    if found:
        raise Problem(
            422,
            "weak_password",
            "Choose another password.",
            errors=[FieldError("password", "weak_password", m) for m in found],
        )


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify(hashed: str | None, password: str) -> bool:
    """Whether the password matches; with no hash, takes as long and says no."""
    try:
        return _HASHER.verify(hashed or _DUMMY, password[:MAX_LENGTH]) and hashed is not None
    except VerificationError, InvalidHashError:
        return False


def needs_rehash(hashed: str) -> bool:
    return _HASHER.check_needs_rehash(hashed)
