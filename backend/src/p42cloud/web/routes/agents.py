"""Agents over the API: administrators make one-time codes, agents enrol with one, fetch their
tasks and send the results back (with their own token, outbound only).

    POST /api/v1/agents/codes             a one-time code to enrol an agent (administrators)
    POST /api/v1/agents/enrol             an agent enrols with its code (anyone, throttled)
    GET  /api/v1/agents/tasks             an agent's waiting tasks (its token)
    POST /api/v1/agents/tasks/<id>        its answer to one of them
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.guards import RECENT
from p42cloud.services.agents.service import enrol, new_code, report, tasks_for
from p42cloud.web.caller import Caller, address, language, who
from p42cloud.web.permissions import PUBLIC, SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])
Who = Annotated[Caller, Depends(who)]


class CodeBody(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]


class EnrolBody(BaseModel):
    code: Annotated[str, Field(max_length=40)]
    version: Annotated[str, Field(max_length=40)] = ""
    host: Annotated[str, Field(max_length=253)] = ""


class ReportBody(BaseModel):
    ok: bool = True
    result: Any = None


@router.post("/codes", status_code=201)
@requires(SIGNED_IN)
async def agent_code(body: CodeBody, request: Request, caller: Who) -> dict[str, Any]:
    """A one-time code (24 hours) to enrol the agent of this name; shown once."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        if not records.access.of("sys.agent", "create"):
            raise Problem(403, "forbidden", "Only administrators enrol agents.")
        if caller.verified_at is None or datetime.now(UTC) - caller.verified_at > RECENT:
            raise Problem(403, "verify_again", "Enter a code from your authenticator app again.")
        code, expires = await new_code(records.conn, body.name)
    return {"code": code, "expires": expires.isoformat()}


@router.post("/enrol", status_code=201)
@requires(PUBLIC)
async def agent_enrol(body: EnrolBody, request: Request) -> dict[str, Any]:
    """The agent's own service account and token, for its code (once)."""
    services: Services = request.app.state.services
    services.rates.take(f"agent:{address(request)}")
    with working_as(Context(channel="api:agent-enrol", access=EVERYTHING)):
        async with transaction(services.engine, "sys") as conn:
            return await enrol(conn, body.code, body.version, body.host, address(request))


@router.get("/tasks")
@requires(SIGNED_IN)
async def agent_tasks(request: Request, caller: Who, version: str | None = None) -> dict[str, Any]:
    """The agent's waiting tasks, now running; asking is also how it's seen."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        return {"tasks": await tasks_for(records.conn, caller.person, version, address(request))}


@router.post("/tasks/{task}")
@requires(SIGNED_IN)
async def agent_report(
    task: UUID, body: ReportBody, request: Request, caller: Who
) -> dict[str, Any]:
    """The agent's answer to one of its running tasks."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        await report(records.conn, caller.person, task, body.ok, body.result)
    return {"ok": True}
