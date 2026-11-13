"""Sealing secrets with the installation's master key: encrypted, and bound to their purpose.

A sealed value names the version of the format, so the scheme can change later. Each purpose
gets its own key derived from the master key, so a value sealed for one purpose (a second factor)
can't be unsealed as another (a connection's password). Without a master key nothing is sealed:
the installer makes one, and the app refuses to store secrets in the clear.
"""

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_VERSION = "v1"
_KEY_BYTES = 32
_NONCE_BYTES = 12


class SealError(Exception):
    """A sealed value that can't be opened: changed, for another purpose, or another key."""


class Sealer:
    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != _KEY_BYTES:
            raise ValueError("the master key must be 32 bytes")
        self._master = master_key

    @classmethod
    def from_file(cls, path: Path | None) -> Sealer:
        if path is None:
            raise RuntimeError("no master key: set P42_MASTER_KEY_FILE (the installer makes one)")
        return cls(base64.b64decode(path.read_text().strip(), validate=True))

    def _key(self, purpose: str) -> AESGCM:
        derived = HKDF(
            algorithm=hashes.SHA256(),
            length=_KEY_BYTES,
            salt=None,
            info=f"p42cloud:{purpose}".encode(),
        ).derive(self._master)
        return AESGCM(derived)

    def seal(self, plain: bytes, purpose: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._key(purpose).encrypt(nonce, plain, purpose.encode())
        return f"{_VERSION}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"

    def unseal(self, sealed: str, purpose: str) -> bytes:
        version, _, body = sealed.partition(":")
        if version != _VERSION:
            raise SealError("unknown format")
        try:
            raw = base64.urlsafe_b64decode(body.encode())
            return self._key(purpose).decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], purpose.encode()
            )
        except (InvalidTag, ValueError) as error:
            raise SealError("can't be opened") from error
