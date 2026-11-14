"""Module routes as records, and how modules declare them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from p42cloud.data.declarations import Table
from p42cloud.data.records import Records
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.string import String
from p42cloud.data.types.translated_text import TranslatedText

METHODS = {
    "get": "GET: read",
    "post": "POST: create or run",
    "put": "PUT: replace",
    "patch": "PATCH: change",
    "delete": "DELETE: delete",
}

MODULE_ROUTE = Table(
    "module_route",
    "Module route",
    {
        # <module> <METHOD> <path>, e.g. "dca GET /widget/profit".
        "name": String("Name", max_length=200, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "method": Choice("Method", METHODS, required=True),
        "path": String("Path", max_length=160, required=True),
        "description": TranslatedText("What it does"),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Module routes",
    display="name",
)

TABLES = [MODULE_ROUTE]


@dataclass(frozen=True)
class RouteRequest:
    """One call of a module route, for the module's code: `Client.of(request)` reads and changes
    records as the caller, in the request's transaction."""

    records: Records
    person: UUID
    params: dict[str, str]  # the path's {parameters}
    query: dict[str, str]
    body: Any = None
    language: str = "en"
    extra: dict[str, Any] = field(default_factory=dict[str, Any])


type Handler = Callable[[RouteRequest], Awaitable[Any]]


@dataclass(frozen=True)
class Route:
    """A module's own route, under /api/v1/<module><path>, for signed-in callers only. What it
    reads and changes goes through the client, so the caller's grants apply."""

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str  # /widget/profit, or with parameters: /plan/{plan}/summary
    handler: Handler
    description: str | dict[str, str] | None = None
