"""Tables: described for the caller (every table), and built by administrators (their own)."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request

from p42cloud.data.presenting import describe
from p42cloud.kernel.context import working_as
from p42cloud.kernel.errors import Problem
from p42cloud.services.tables.service import retire_table, save_table, table_from
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["tables"])
Who = Annotated[Caller, Depends(who)]


@router.get("/meta/{module}/{table}")
@requires(SIGNED_IN)
async def describe_table(module: str, table: str, request: Request, caller: Who) -> dict[str, Any]:
    """A table as the caller sees it: labels in their language, its fields with their types,
    options and choices, and what the caller may do (on some records; per record see
    `_access`)."""
    services: Services = request.app.state.services
    with working_as(caller.context(language(request))):
        catalog = await services.cache.catalog()
    found = catalog.table(f"{module}.{table}")
    if found is None or found.switched_off or found.retired:
        raise Problem(404, "no_table", "There's no such table.", detail=f"{module}.{table}")
    return describe(found, caller.access, language(request))


@router.post("/tables")
@requires(SIGNED_IN)
async def save(
    body: Annotated[dict[str, Any], Body()], request: Request, caller: Who
) -> dict[str, Any]:
    """Makes or changes one of the administrators' own tables (`u.<name>`), with its fields:
    {"name", "label", "label_plural", "display", "fields": {name: {type, label, options,
    required, unique, default, help}}}. Administrators only, with a recent second factor."""
    services: Services = request.app.state.services
    table = table_from(body)
    with working_as(caller.context(language(request))):
        done = await save_table(services.owner, table)
    return {"table": f"u.{table.name}", "changed": done}


@router.delete("/tables/{name}")
@requires(SIGNED_IN)
async def retire(name: str, request: Request, caller: Who) -> dict[str, str]:
    """Retires one of the administrators' tables: hidden and read-only, its data kept."""
    services: Services = request.app.state.services
    with working_as(caller.context(language(request))):
        await retire_table(services.owner, name)
    return {"table": f"u.{name}", "state": "retired"}
