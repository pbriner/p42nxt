"""An OpenID Connect provider in the test process: discovery, keys, codes, ID tokens.

It checks what a real one checks (PKCE, the client, the redirect address) and can be told to
misbehave, to see that P42cloud refuses what it should.
"""

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm


def _rsa() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class Grant:
    claims: dict[str, Any]
    challenge: str
    client_id: str
    redirect_uri: str


@dataclass
class FakeProvider:
    issuer: str = field(default_factory=lambda: f"https://idp-{uuid.uuid4().hex[:6]}.test")
    client_id: str = "p42cloud"
    client_secret: str = "s3cret"
    key: rsa.RSAPrivateKey = field(default_factory=_rsa)
    kid: str = "key-1"
    grants: dict[str, Grant] = field(default_factory=dict[str, Grant])
    # Misbehaviour for the next token: {"alg": "HS256"}, {"iss": "..."}, {"key": other} …
    tamper: dict[str, Any] = field(default_factory=dict[str, Any])

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{self.issuer}/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/keys",
                },
            )
        if url == f"{self.issuer}/keys":
            public = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
            return httpx.Response(200, json={"keys": [public | {"kid": self.kid, "alg": "RS256"}]})
        if url == f"{self.issuer}/token" and request.method == "POST":
            return self._token(parse_qs(request.content.decode()))
        return httpx.Response(404)

    def authorize(self, redirect: str, claims: dict[str, Any]) -> tuple[str, str]:
        """What the provider does when the person signs in there: returns (state, code)."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(redirect).query).items()}
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.grants[code] = Grant(
            claims | {"nonce": query["nonce"]},
            query["code_challenge"],
            query["client_id"],
            query["redirect_uri"],
        )
        return query["state"], code

    def _token(self, form: dict[str, list[str]]) -> httpx.Response:
        given = {k: v[0] for k, v in form.items()}
        grant = self.grants.pop(given.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        verifier = hashlib.sha256(given["code_verifier"].encode()).digest()
        if (
            base64.urlsafe_b64encode(verifier).decode().rstrip("=") != grant.challenge
            or given.get("client_id") != grant.client_id
            or given.get("client_secret") != self.client_secret
            or given.get("redirect_uri") != grant.redirect_uri
        ):
            return httpx.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        payload = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
        } | grant.claims
        tamper = dict(self.tamper)
        alg = tamper.pop("alg", "RS256")
        key: Any = tamper.pop("key", self.key)
        payload |= tamper
        token = jwt.encode(payload, key, algorithm=alg, headers={"kid": self.kid})
        return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
