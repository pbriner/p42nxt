"""Writing connection types into records, checking connections and sealing their secrets, and
the ready client module code uses.

    async with connect(records, "dca.kraken", owner=plan_owner) as http:
        answer = await http.post("/0/private/AddOrder", data={...})

A person may use their own connections; a module's jobs may use connections of the module's own
types for the people they work for. The client reaches only the type's hosts, through the
platform's safe client, and signs in with the secrets from the vault.
"""

import asyncio
import base64
import ipaddress
import re
import ssl
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.declared import label_of, sync
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.records import Records
from p42cloud.kernel.context import current
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.kernel.outgoing import host_allowed, never_local, safe_client, within
from p42cloud.kernel.secrets import SealError, process_sealer
from p42cloud.services.connections.models import AUTHS, ConnectionType

TYPE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
SECRET_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RETRIED = frozenset({429, 502, 503, 504})
MAX_WAIT = 30.0
ATTEMPTS = 3

# The installed modules' connection types, by name: (module, type). Filled when composing.
TYPES: dict[str, tuple[str, ConnectionType]] = {}
# For tests only: a transport standing in for the outside system, per type (still guarded).
TEST_TRANSPORTS: dict[str, httpx.AsyncBaseTransport] = {}


def register(module: str, types: tuple[ConnectionType, ...]) -> None:
    for kind in types:
        TYPES[f"{module}.{kind.name}"] = (module, kind)


def _invalid(name: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(name, "invalid", message)],
    )


# ---- declaring ----


def _url_problem(url: str, allowed: tuple[str, ...]) -> str | None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "An https address."
    if not host_allowed(parts.hostname, allowed):
        return f"{parts.hostname} isn't one of the hosts this type may reach."
    return None


def _problems(kind: ConnectionType) -> list[FieldError]:
    where = f"connection_types.{kind.name}"
    found: list[FieldError] = []
    if not TYPE_NAME.match(kind.name):
        found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
    if kind.auth not in AUTHS:
        found.append(FieldError(where, "bad_auth", f"One of {', '.join(AUTHS)}."))
    if not kind.allowed_hosts and not kind.local:
        found.append(FieldError(where, "no_hosts", "A type names the hosts it may reach."))
    if kind.auth == "signed" and kind.sign is None:
        found.append(FieldError(where, "no_sign", "A signed type signs its requests (sign)."))
    needed = {"bearer": {"token"}, "basic": {"username", "password"}, "api_key": {"key"}}
    if not needed.get(kind.auth, set()) <= set(kind.secrets):
        found.append(
            FieldError(where, "missing_secret", f"{kind.auth} needs {sorted(needed[kind.auth])}.")
        )
    found += [
        FieldError(where, "bad_secret", f"Not a secret's name: {s!r}.")
        for s in kind.secrets
        if not SECRET_NAME.match(s)
    ]
    if kind.base_url and (problem := _url_problem(kind.base_url, kind.allowed_hosts)):
        found.append(FieldError(where, "bad_url", problem))
    return found


async def write_types(
    conn: AsyncConnection, module: str, types: tuple[ConnectionType, ...]
) -> None:
    found = [p for k in types for p in _problems(k)]
    if len({k.name for k in types}) != len(types):
        found.append(FieldError("connection_types", "duplicate", "A type's name is used twice."))
    if found:
        raise Problem(
            422, "invalid_connection_types", "The connection types can't be used.", errors=found
        )
    rows = [
        {
            "name": f"{module}.{k.name}",
            "label": label_of(k.label),
            "auth": k.auth,
            "allowed_hosts": list(k.allowed_hosts),
            "secrets": list(k.secrets) or None,
            "base_url": k.base_url or None,
            "api_key_header": k.api_key_header if k.auth == "api_key" else None,
            "local": k.local,
        }
        for k in types
    ]
    await sync(
        conn, "sys.connection_type", module, rows, frozenset({"label", "allowed_hosts", "secrets"})
    )


# ---- what people write ----


def _type(name: Any) -> tuple[str, ConnectionType]:
    found = TYPES.get(str(name))
    if found is None:
        raise _invalid("type", "No such connection type is installed.")
    return found


async def _check_connection(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["owner"] = change.person
        _, kind = _type(change.values.get("type"))
        if not change.values.get("base_url"):
            change.values["base_url"] = kind.base_url or None
        if not change.values["base_url"] and not kind.local:
            raise _invalid("base_url", "This kind of connection needs its address.")
        change.values["health"] = "unknown"
        if kind.shared:
            await _check_shared(change, str(change.values.get("type")))
    elif "type" in change.values:
        raise _invalid("type", "A connection stays of its type.")
    else:
        found = await change.records.conn.scalar(
            text("SELECT type FROM sys.connection WHERE sys_id = :id"), {"id": change.sys_id}
        )
        _, kind = _type(found)
    _check_where(change, kind)
    if {"health", "checked", "health_message"} & set(
        change.values
    ) and change.operation != "create":
        raise _invalid("health", "A connection's health comes from its check.")


def _check_where(change: Change, kind: ConnectionType) -> None:
    """Where it may reach: a local type's ranges, else an https address on the type's hosts."""
    if kind.local:
        _check_local(change)
        return
    if change.values.get("local_ranges") or change.values.get("trusted_certificate"):
        raise _invalid(
            "local_ranges",
            "Only connections to your own network have local ranges or a pinned certificate.",
        )
    url = change.values.get("base_url")
    if url is not None and (problem := _url_problem(str(url), kind.allowed_hosts)):
        raise _invalid("base_url", problem)


def _pinned(pem: str) -> ssl.SSLContext:
    """Trusts this one certificate only (a device's own), reached by its address."""
    try:
        context = ssl.create_default_context(cadata=pem)
    except ssl.SSLError as error:
        raise Problem(
            409, "bad_certificate", "The connection's certificate can't be read."
        ) from error
    context.check_hostname = False
    return context


MIN_PREFIX = {4: 16, 6: 48}  # no wider than a /16 (IPv4) or a /48 (IPv6)


def _check_local(change: Change) -> None:
    """A local connection's ranges: inside a network, not too wide, its address within them.
    It's the one way past the safe client's rule, so it's checked strictly (and audited, as a
    record)."""
    ranges = change.values.get("local_ranges")
    if ranges is None and change.operation != "create":
        return
    if not isinstance(ranges, list) or not ranges:
        raise _invalid("local_ranges", "Name the ranges it may reach, like 192.168.1.0/24.")
    checked: list[str] = []
    for given in cast(list[Any], ranges):
        try:
            net = ipaddress.ip_network(str(given).strip(), strict=False)
        except ValueError as error:
            raise _invalid("local_ranges", f"Not a range: {given}.") from error
        if never_local(net) or net.prefixlen < MIN_PREFIX[net.version]:
            raise _invalid(
                "local_ranges",
                f"{net}: a range inside your network, at most a /16 (not this machine itself).",
            )
        checked.append(str(net))
    change.values["local_ranges"] = checked
    pem = change.values.get("trusted_certificate")
    if pem:
        try:
            ssl.create_default_context(cadata=str(pem))
        except ssl.SSLError as error:
            raise _invalid("trusted_certificate", "Not a certificate (PEM).") from error
    url = change.values.get("base_url")
    host = urlsplit(str(url)).hostname if url else None
    if host is not None and not within(host, checked):
        raise _invalid("base_url", "The address is in one of its local ranges.")


async def _check_shared(change: Change, type_name: str) -> None:
    """The installation's one connection of a type: only administrators (who may change
    settings) set it up, and there's only one."""
    if not change.records.access.of("sys.setting", "write"):
        raise Problem(403, "forbidden", "Only administrators set up this connection.")
    taken = await change.records.conn.scalar(
        text("SELECT count(*) FROM sys.connection WHERE type = :t AND deleted IS NULL"),
        {"t": type_name},
    )
    if taken:
        raise _invalid("type", "The installation has this connection already.")


def purpose(connection: uuid.UUID, name: str) -> str:
    """What a secret is sealed for: one connection's one secret, never another's."""
    return f"connection:{connection}:{name}"


async def _seal_secret(change: Change) -> None:
    """A connection's secret: one the type asks for, on one's own connection, sealed at once."""
    if change.operation in ("delete", "restore"):
        return
    conn = change.records.conn
    if "told" in change.sent:
        raise _invalid("told", "Set by the reminder.")
    if {"value", "expires"} & set(change.values):
        change.values["told"] = None  # a new secret or date: reminded again before it ends
    if change.operation == "create":
        change.values["owner"] = change.person
        connection = change.values.get("connection")
        name = change.values.get("name")
    else:
        if {"connection", "name"} & set(change.values):
            raise _invalid("name", "A secret stays with its connection and name.")
        row: Any = (
            await conn.execute(
                text("SELECT connection, name FROM sys.connection_secret WHERE sys_id = :id"),
                {"id": change.sys_id},
            )
        ).first()
        connection, name = row.connection, row.name
    found: Any = (
        await conn.execute(
            text("SELECT owner, type FROM sys.connection WHERE sys_id = :id AND deleted IS NULL"),
            {"id": connection},
        )
    ).first()
    if found is None or found.owner != change.person:
        raise _invalid("connection", "Not one of your connections.")
    _, kind = _type(found.type)
    if name not in kind.secrets:
        raise _invalid("name", f"This type's secrets are: {', '.join(kind.secrets) or 'none'}.")
    if "value" in change.values:
        value = change.values["value"]
        if not isinstance(value, str) or not value:
            raise _invalid("value", "A secret's value.")
        change.values["value"] = process_sealer().seal(
            value.encode(), purpose(uuid.UUID(str(connection)), str(name))
        )


TABLE_HOOKS = (
    TableHook("sys.connection", before=_check_connection),
    TableHook("sys.connection_secret", before=_seal_secret),
)


# ---- using a connection ----


class _Auth(httpx.Auth):
    def __init__(self, kind: ConnectionType, secrets: dict[str, str]) -> None:
        self._kind = kind
        self._secrets = secrets

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        kind, secrets = self._kind, self._secrets
        if kind.auth == "bearer":
            request.headers["Authorization"] = f"Bearer {secrets.get('token', '')}"
        elif kind.auth == "basic":
            pair = f"{secrets.get('username', '')}:{secrets.get('password', '')}".encode()
            request.headers["Authorization"] = f"Basic {base64.b64encode(pair).decode()}"
        elif kind.auth == "api_key":
            request.headers[kind.api_key_header] = secrets.get("key", "")
        elif kind.auth == "signed" and kind.sign is not None:
            kind.sign(request, secrets)
        yield request


class _Retrying(httpx.AsyncBaseTransport):
    """Reading calls tried again when the other side is busy (429, 502-504), as it asks."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(ATTEMPTS):
            response = await self._inner.handle_async_request(request)
            if (
                request.method not in ("GET", "HEAD")
                or response.status_code not in RETRIED
                or attempt == ATTEMPTS - 1
            ):
                return response
            await response.aclose()
            wait = response.headers.get("retry-after", "")
            await asyncio.sleep(min(MAX_WAIT, float(wait) if wait.isdigit() else 2.0**attempt))
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self._inner.aclose()


def _may_use(records: Records, module: str, owner: uuid.UUID) -> bool:
    """One's own connections; a module's jobs, its own types' connections for anyone."""
    if records.access.unrestricted:
        return True
    if records.situation.person == owner:
        return True
    return current().channel.startswith(f"job:{module}.")


@asynccontextmanager
async def connect(
    records: Records,
    type_name: str,
    *,
    owner: uuid.UUID | None = None,
    label: str | None = None,
    connection: uuid.UUID | None = None,
) -> AsyncGenerator[httpx.AsyncClient]:
    """A ready client for someone's connection of a type (the person's own by default); one
    named connection of theirs when given (never another person's, nor of another type)."""
    module, kind = _type(type_name)
    who = owner or records.situation.person
    if kind.shared:
        # Whoever set it up: it's the installation's, used by the type's module only.
        await switch_role(records.conn, "sys")
        who = (
            await records.conn.scalar(
                text(
                    "SELECT owner FROM sys.connection WHERE type = :t AND deleted IS NULL"
                    " ORDER BY created LIMIT 1"
                ),
                {"t": type_name},
            )
        ) or who
        if not _may_use(records, module, who):
            raise Problem(403, "forbidden", "Only the platform uses this connection.")
    elif not _may_use(records, module, who):
        raise Problem(403, "forbidden", "You may not use this connection.")
    conn = records.conn
    await switch_role(conn, "sys")
    row: Any = (
        await conn.execute(
            text(
                "SELECT sys_id, base_url, local_ranges, trusted_certificate FROM sys.connection"
                " WHERE owner = :o"
                " AND type = :t"
                " AND deleted IS NULL AND (CAST(:l AS text) IS NULL OR label = :l)"
                " AND (CAST(:c AS uuid) IS NULL OR sys_id = CAST(:c AS uuid))"
                " ORDER BY created LIMIT 1"
            ),
            {"o": who, "t": type_name, "l": label, "c": connection},
        )
    ).first()
    if row is None:
        raise Problem(404, "no_connection", "There's no such connection; set one up first.")
    sealer = process_sealer()
    secrets: dict[str, str] = {}
    for item in await conn.execute(
        text(
            "SELECT name, value FROM sys.connection_secret"
            " WHERE connection = :c AND deleted IS NULL"
        ),
        {"c": row.sys_id},
    ):
        try:
            secrets[item.name] = sealer.unseal(item.value, purpose(row.sys_id, item.name)).decode()
        except SealError as error:
            raise Problem(
                409, "secret_unreadable", f"The secret {item.name!r} can't be opened; set it again."
            ) from error
    trusted = row.trusted_certificate if kind.local else None
    inner = TEST_TRANSPORTS.get(type_name) or (
        httpx.AsyncHTTPTransport(retries=0, verify=_pinned(str(trusted)) if trusted else True)
    )
    client = safe_client(
        allowed_hosts=kind.allowed_hosts,
        base_url=row.base_url or "",
        auth=_Auth(kind, secrets),
        transport=_Retrying(inner),
        local_ranges=list(row.local_ranges or []) if kind.local else None,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def check(records: Records, connection: uuid.UUID) -> str:
    """Runs the type's check on one of one's connections and records what it found."""
    conn = records.conn
    await switch_role(conn, "sys")
    row: Any = (
        await conn.execute(
            text(
                "SELECT owner, type, label FROM sys.connection"
                " WHERE sys_id = :id AND deleted IS NULL"
            ),
            {"id": connection},
        )
    ).first()
    if row is None:
        raise Problem(404, "not_found", "There's no such connection.")
    _, kind = _type(row.type)
    health, message = "ok", "It answers."
    try:
        async with connect(records, row.type, owner=row.owner, label=row.label) as http:
            if kind.check is not None:
                message = await kind.check(http)
    except (httpx.HTTPError, Problem) as error:
        health = "failed"
        message = cast(str, getattr(error, "title", None) or str(error) or type(error).__name__)
    await switch_role(conn, "sys")
    await conn.execute(
        text(
            "UPDATE sys.connection SET health = :h, checked = :now, health_message = :m"
            " WHERE sys_id = :id"
        ),
        {"h": health, "now": datetime.now(UTC), "m": message[:2000], "id": connection},
    )
    return message if health == "ok" else f"It doesn't work: {message}"
