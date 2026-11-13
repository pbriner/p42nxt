"""What's checked when a provider is saved through the records service."""

import re
from typing import Any, cast
from urllib.parse import urlsplit

from p42cloud.data.conditions import SYSTEM_FIELDS
from p42cloud.data.hooks import Change, TableHook
from p42cloud.kernel.errors import FieldError, Problem

DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
# Fields a provider may never set: who someone is and what they may do aren't the provider's.
# The email comes only through linking, where it must be confirmed.
NOT_FROM_CLAIMS = frozenset({"kind", "active", "emergency_access", "email"})


def _domains(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(d, str) and DOMAIN.match(d) for d in cast(list[Any], value)
    )


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _provider(change: Change) -> None:
    values = change.values
    if "client_secret" in values:
        raise _invalid("client_secret", "Set the client secret on its own; it's sealed first.")
    issuer = values.get("issuer")
    if isinstance(issuer, str):
        address = urlsplit(issuer)
        if address.scheme != "https" and address.hostname != "localhost":
            raise _invalid("issuer", "The issuer must use https.")
    domains = values.get("domains")
    if domains is not None and not _domains(domains):
        raise _invalid("domains", "A list of domains in lower case, like example.com.")
    claims = values.get("claims")
    if claims is not None:
        person = change.records.catalog.table("sys.person")
        fields = {f.name for f in person.active_fields()} if person else set[str]()
        if not isinstance(claims, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in cast(dict[Any, Any], claims).items()
        ):
            raise _invalid("claims", 'Person fields and the claims filling them: {"name": "name"}.')
        for name in cast(dict[str, str], claims):
            if name not in fields or name in SYSTEM_FIELDS or name in NOT_FROM_CLAIMS:
                raise _invalid("claims", f"{name!r} can't be filled from a provider's claims.")


TABLE_HOOKS = (TableHook("sys.identity_provider", before=_provider),)
