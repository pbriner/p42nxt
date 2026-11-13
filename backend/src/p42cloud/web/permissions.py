"""Every route declares who may call it; nothing is reachable by default.

    @router.get("/api/v1/me")
    @requires(SIGNED_IN)
    async def me(...): ...

A test refuses any route without a declaration. What signed-in people may do with records is
decided by their grants, in the records service.
"""

from collections.abc import Callable
from typing import Any

PUBLIC = "public"  # anyone, signed in or not: health, signing in
SIGNED_IN = "signed_in"  # a session or an API token; the grants decide the rest

_ATTRIBUTE = "p42_permission"


def requires[F: Callable[..., Any]](permission: str) -> Callable[[F], F]:
    if permission not in (PUBLIC, SIGNED_IN):
        raise ValueError(f"unknown permission {permission!r}")

    def mark(endpoint: F) -> F:
        setattr(endpoint, _ATTRIBUTE, permission)
        return endpoint

    return mark


def declared(endpoint: Callable[..., Any]) -> str | None:
    found = getattr(endpoint, _ATTRIBUTE, None)
    return found if isinstance(found, str) else None
