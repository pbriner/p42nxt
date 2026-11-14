"""Connection types (declared by modules), connections and their secrets (each person's own)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.secret import Secret
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText
from p42cloud.data.types.url import Url

AUTHS = {
    "none": "None",
    "bearer": "A token (Bearer)",
    "basic": "User and password",
    "api_key": "A key in a header",
    "signed": "Signed by the module",
}

CONNECTION_TYPE = Table(
    "connection_type",
    "Connection type",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "label": TranslatedText("Label", required=True),
        "auth": Choice("Signing in", AUTHS, required=True, default="none"),
        # The only hosts its connections may reach.
        "allowed_hosts": Json("Allowed hosts", required=True),
        "secrets": Json("Secrets it needs"),
        "base_url": Url("Usual address"),
        "api_key_header": String("Key header", max_length=80),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Connection types",
    display="label",
)

CONNECTION = Table(
    "connection",
    "Connection",
    {
        "owner": Reference("Owner", "sys.person", required=True),
        "type": String("Type", max_length=120, required=True),
        "label": String("Name", max_length=120, required=True),
        "base_url": Url("Address", required=True),
        "health": Choice(
            "Works",
            {"unknown": "Not checked", "ok": "Yes", "failed": "No"},
            required=True,
            default="unknown",
            tones={"ok": "primary", "failed": "error", "unknown": "muted"},
        ),
        "checked": DateTime("Checked"),
        "health_message": Text("Last check said"),
    },
    label_plural="Connections",
    owner="owner",
    display="label",
    unique_together=(("owner", "type", "label"),),
)

CONNECTION_SECRET = Table(
    "connection_secret",
    "Connection secret",
    {
        "owner": Reference("Owner", "sys.person", required=True),
        "connection": Reference("Connection", "sys.connection", required=True),
        "name": String("Name", max_length=60, required=True),
        # Sealed with the master key, bound to its connection and name; never read back.
        "value": Secret("Value", required=True),
    },
    label_plural="Connection secrets",
    owner="owner",
    display="name",
    unique_together=(("connection", "name"),),
)

TABLES = [CONNECTION_TYPE, CONNECTION, CONNECTION_SECRET]

type Sign = Callable[[httpx.Request, dict[str, str]], None]
type Check = Callable[[httpx.AsyncClient], Awaitable[str]]


@dataclass(frozen=True)
class ConnectionType:
    """An outside system a module talks to. Its connections reach only `allowed_hosts`; module
    code gets a ready client and never sees a secret."""

    name: str  # unique in the module; the record is named <module>.<name>
    label: str | dict[str, str]
    allowed_hosts: tuple[str, ...]  # api.example.com, or *.example.com for its subdomains
    auth: Literal["none", "bearer", "basic", "api_key", "signed"] = "none"
    # bearer: token; basic: username, password; api_key: key; signed: whatever `sign` uses.
    secrets: tuple[str, ...] = ()
    base_url: str = ""
    api_key_header: str = "X-Api-Key"
    sign: Sign | None = None  # signed: adds headers to each request from the secrets
    check: Check | None = None  # whether a connection works; says what it found
    # One connection for the whole installation (a bot, a company system), set up by
    # administrators and used by the type's module only; not one per person.
    shared: bool = False
