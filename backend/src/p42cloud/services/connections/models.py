"""Connection types (declared by modules), connections and their secrets (each person's own)."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.date import Date
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
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "label": TranslatedText("Label", required=True),
        "auth": Choice("Signing in", AUTHS, required=True, default="none"),
        # The only hosts its connections may reach.
        "allowed_hosts": Json("Allowed hosts", required=True),
        "secrets": Json("Secrets it needs"),
        "base_url": Url("Usual address"),
        "api_key_header": String("Key header", max_length=80),
        "local": Boolean("Inside your own network", required=True, default=False),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Connection types",
    display="label",
)

CONNECTION = Table(
    "connection",
    "Connection",
    {
        # Whoever makes it (the table's check sets it); never changed.
        "owner": Reference("Owner", "sys.person", required=True, read_only=True),
        "type": String("Type name", max_length=120, required=True),
        "type_ref": Reference("Type", "sys.connection_type", key="type"),
        "label": String("Name", max_length=120, required=True),
        # Empty: the type's usual address (the table's check fills it in, or refuses).
        "base_url": Url("Address", help="Empty: the usual address of its type."),
        # A local type's connection: the only ranges inside the network it may reach.
        "local_ranges": Json(
            "Local ranges",
            lines=True,
            help="Only for devices in your own network: one range per line, like"
            " 192.168.1.0/24. Nothing else is reached.",
        ),
        # A device in one's own network often has its own certificate (a router, a hypervisor):
        # trusted exactly, this one only, instead of a certificate authority.
        "trusted_certificate": Text(
            "Its own certificate (PEM)",
            help="Only for devices in your own network with a self-made certificate: paste it,"
            " and only that one is trusted.",
        ),
        "health": Choice(
            "Works",
            {"unknown": "Not checked", "ok": "Yes", "failed": "No"},
            required=True,
            default="unknown",
            tones={"ok": "primary", "failed": "error", "unknown": "muted"},
            read_only=True,  # the Check action's
        ),
        "checked": DateTime("Checked", read_only=True),
        "health_message": Text("Last check said", read_only=True),
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
        "expires": Date(
            "Expires", help="When the outside system stops taking it: you're reminded before."
        ),
        # The day its owner was last reminded (two weeks before, then on the day).
        "told": Date("Reminded", read_only=True),
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
    # Devices inside the installation's own network (smart plugs, a router, a hypervisor): a
    # connection reaches only the ranges an administrator names on it, over http or https.
    # `allowed_hosts` doesn't apply then.
    local: bool = False
