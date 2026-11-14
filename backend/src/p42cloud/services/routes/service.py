"""Checking and recording modules' routes; the registry the web app mounts them from."""

import re

from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.declared import label_of, sync
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.services.routes.models import METHODS, Route

SEGMENT = re.compile(r"^([a-z][a-z0-9_-]{0,39}|\{[a-z][a-z0-9_]{0,39}\})$")
MAX_SEGMENTS = 6
# The generic routes' words after /api/v1/<module>/<table>: a module route can't start with them.
RESERVED = frozenset({"action", "aggregate", "external", "bulk", "options"})

# The installed modules' routes: (module, route). Filled when composing.
ROUTES: list[tuple[str, Route]] = []


def register(module: str, routes: tuple[Route, ...]) -> None:
    ROUTES[:] = [(m, r) for m, r in ROUTES if m != module]
    ROUTES.extend((module, r) for r in routes)


def route_problems(routes: tuple[Route, ...], tables: set[str]) -> list[FieldError]:
    """A route may not hide a generic one: its first part is no table of the module."""
    found: list[FieldError] = []
    for route in routes:
        where = f"routes.{route.method} {route.path}"
        parts = route.path.split("/")
        if route.method.lower() not in METHODS:
            found.append(FieldError(where, "bad_method", "GET, POST, PUT, PATCH or DELETE."))
        if (
            not route.path.startswith("/")
            or len(parts) < 2  # noqa: PLR2004 - "" and the first segment
            or len(parts) > MAX_SEGMENTS + 1
            or not all(SEGMENT.match(p) for p in parts[1:])
        ):
            found.append(
                FieldError(where, "bad_path", "Parts of a-z, 0-9, _ and -, or {parameter}.")
            )
            continue
        first = parts[1]
        if first.startswith("{") or first in tables or first in RESERVED:
            found.append(
                FieldError(where, "taken", f"/{first} is the module's table or a generic route.")
            )
    keys = [(r.method, r.path) for r in routes]
    if len(set(keys)) != len(keys):
        found.append(FieldError("routes", "duplicate", "A route is declared twice."))
    return found


async def write_routes(
    conn: AsyncConnection, module: str, routes: tuple[Route, ...], tables: set[str]
) -> None:
    found = route_problems(routes, tables)
    if found:
        raise Problem(422, "invalid_routes", "The routes can't be used.", errors=found)
    rows = [
        {
            "name": f"{module} {r.method} {r.path}",
            "method": r.method.lower(),
            "path": r.path,
            "description": label_of(r.description),
        }
        for r in routes
    ]
    await sync(conn, "sys.module_route", module, rows, frozenset({"description"}))
