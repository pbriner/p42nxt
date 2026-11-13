"""A passkey in software, for tests: answers what a browser and a phone or security key would.

It makes a P-256 key, registers it ("none" attestation), and signs challenges with it, following
the WebAuthn spec: authenticator data, the client data's hash, an ECDSA signature.
"""

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

_UP, _UV, _AT = 0x01, 0x04, 0x40


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class SoftKey:
    rp_id: str
    origin: str
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    key_id: bytes = field(default_factory=lambda: os.urandom(32))
    counter: int = 0

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        data = {
            "type": kind,
            "challenge": challenge,
            "origin": origin or self.origin,
            "crossOrigin": False,
        }
        return json.dumps(data).encode()

    def _auth_data(self, flags: int, extra: bytes = b"", rp_id: str | None = None) -> bytes:
        self.counter += 1
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack(">I", self.counter) + extra

    def register(self, options: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32), -3: numbers.y.to_bytes(32)}
        )
        attested = bytes(16) + struct.pack(">H", len(self.key_id)) + self.key_id + cose
        auth = self._auth_data(_UP | _UV | _AT, attested)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        client = self._client_data("webauthn.create", options["challenge"], origin)
        return {
            "id": _b64(self.key_id),
            "rawId": _b64(self.key_id),
            "type": "public-key",
            "response": {"clientDataJSON": _b64(client), "attestationObject": _b64(attestation)},
        }

    def sign(
        self, options: dict[str, Any], *, origin: str | None = None, rp_id: str | None = None
    ) -> dict[str, Any]:
        client = self._client_data("webauthn.get", options["challenge"], origin)
        auth = self._auth_data(_UP | _UV, rp_id=rp_id)
        signature = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": _b64(self.key_id),
            "rawId": _b64(self.key_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": _b64(client),
                "authenticatorData": _b64(auth),
                "signature": _b64(signature),
            },
        }

    def offered(self, options: dict[str, Any]) -> bool:
        """Whether the options let this key answer (it's among the allowed ones)."""
        return any(_unb64(c["id"]) == self.key_id for c in options.get("allowCredentials", []))
