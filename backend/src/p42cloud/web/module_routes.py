"""Mounting the modules' own routes at start, before the generic record routes.

Each runs for a signed-in caller in one transaction, with the module switched on; its code gets
the caller's records (their grants apply) and answers JSON.
"""

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.services.routes.models import Route, RouteRequest
from p42cloud.services.routes.service import ROUTES
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

Who = Annotated[Caller, Depends(who)]
MAX_BODY = 1_000_000
_PARAM = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _endpoint(module: str, route: Route) -> Any:
    names = _PARAM.findall(route.path)

    @requires(SIGNED_IN)
    async def endpoint(request: Request, caller: Who) -> Any:
        services: Services = request.app.state.services
        body: Any = None
        if route.method != "GET":
            declared_size = request.headers.get("content-length", "0")
            if not declared_size.isdigit() or int(declared_size) > MAX_BODY:
                raise Problem(413, "too_large", "The request is too large.")
            raw = await request.body()
            if len(raw) > MAX_BODY:
                raise Problem(413, "too_large", "The request is too large.")
            if raw:
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError as error:
                    raise Problem(400, "bad_json", "The body must be JSON.") from error
        async with services.records(caller.context(language(request))) as records:
            await switch_role(records.conn, "sys")
            on = await records.conn.scalar(
                text("SELECT enabled FROM sys.module WHERE name = :m AND deleted IS NULL"),
                {"m": module},
            )
            if not on:
                raise Problem(404, "not_found", "There's no such route.")
            return await route.handler(
                RouteRequest(
                    records=records,
                    person=caller.person,
                    params={n: str(request.path_params[n]) for n in names},
                    query=dict(request.query_params),
                    body=body,
                    language=records.situation.language,
                )
            )

    endpoint.__name__ = f"{module}_{route.method.lower()}_{route.path.strip('/').replace('/', '_')}"
    return endpoint


def mount(router: APIRouter) -> None:
    """Fills the app's module router (included before the generic `/api/v1/<module>/...`
    routes) with the registered module routes."""
    for module, route in ROUTES:
        router.add_api_route(
            f"/api/v1/{module}{route.path}",
            _endpoint(module, route),
            methods=[route.method],
            tags=[module],
            summary=route.description if isinstance(route.description, str) else None,
        )
