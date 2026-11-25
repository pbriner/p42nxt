"""Approvals over the API: what waits for the caller, a record's approvals, and deciding.

    GET  /api/v1/approvals/mine                          what waits for the caller's approval
    GET  /api/v1/<module>/<table>/<id>/approvals         a record's approvals (who may read it)
    POST /api/v1/approvals/<id>/approve                  a yes (`note` optional)
    POST /api/v1/approvals/<id>/reject                   a no (`note` required: why)
    POST /api/v1/approvals/<id>/withdraw                 who asked takes it back

Asking for approval is pressing the move's button (`POST …/action/<module>.move_<name>`).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from p42cloud.kernel.errors import Problem
from p42cloud.services.approvals.service import decide, mine, of_record, withdraw
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["approvals"])
Who = Annotated[Caller, Depends(who)]


class DecisionBody(BaseModel):
    note: Annotated[str, Field(max_length=2000)] | None = None


@router.get("/approvals/mine")
@requires(SIGNED_IN)
async def my_approvals(request: Request, caller: Who) -> dict[str, Any]:
    """What waits for the caller's approval (or theirs as a deputy), oldest first."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        return {"approvals": await mine(records)}


@router.post("/approvals/{approval}/{decision}")
@requires(SIGNED_IN)
async def decide_approval(
    approval: UUID, decision: str, body: DecisionBody, request: Request, caller: Who
) -> dict[str, Any]:
    """Approve or reject as one of the current step's approvers; or withdraw as who asked."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        if decision == "withdraw":
            return await withdraw(records, approval)
        if decision not in ("approve", "reject"):
            raise Problem(404, "not_found", "Approve, reject or withdraw.")
        return await decide(records, approval, decision, body.note)


@router.get("/{module}/{table}/{sys_id}/approvals")
@requires(SIGNED_IN)
async def record_approvals(
    module: str, table: str, sys_id: UUID, request: Request, caller: Who
) -> dict[str, Any]:
    """A record's approvals, newest first, with what the caller may do."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        found = records.catalog.table(f"{module}.{table}")
        if found is None:
            raise Problem(404, "not_found", "There's no such table.")
        return {"approvals": await of_record(records, found, sys_id)}
