"""Running UI actions: on one record, or on many at once (a result per record).

    POST /api/v1/<module>/<table>/<id>/action/<name>   {"inputs": {...}}   If-Match optional
    POST /api/v1/<module>/<table>/action/<name>        {"ids": [...], "inputs": {...}}

Which actions a record offers comes with it, as `_actions`; their definitions are records of
`sys.ui_action`. Running checks everything again.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from p42cloud.ui.actions.service import MAX_BULK, run, run_many
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["actions"])
Who = Annotated[Caller, Depends(who)]


class Run(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict[str, Any])


class RunMany(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=MAX_BULK)
    inputs: dict[str, Any] = Field(default_factory=dict[str, Any])


@router.post("/{module}/{table}/{sys_id}/action/{name}")
@requires(SIGNED_IN)
async def run_action(
    module: str,
    table: str,
    sys_id: UUID,
    name: str,
    body: Run,
    request: Request,
    caller: Who,
    if_match: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Runs one action on one record: a message, and where to go next."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        return await run(
            records, f"{module}.{table}", name, sys_id, given=body.inputs, etag=if_match
        )


@router.post("/{module}/{table}/action/{name}")
@requires(SIGNED_IN)
async def run_action_on_many(
    module: str, table: str, name: str, body: RunMany, request: Request, caller: Who
) -> dict[str, Any]:
    """Runs one action on each record, each on its own; a result per record."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        results = await run_many(records, f"{module}.{table}", name, body.ids, given=body.inputs)
    return {"results": results}
