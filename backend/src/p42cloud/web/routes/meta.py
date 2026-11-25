"""The API about itself and the caller: health, who am I, undo."""

from importlib.metadata import version
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from p42cloud.data.undo import undo
from p42cloud.kernel.errors import Problem
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import PUBLIC, SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["platform"])
Who = Annotated[Caller, Depends(who)]


@router.get("/health")
@requires(PUBLIC)
async def health() -> dict[str, str]:
    """Whether the app is up (it answers only once the database is ready and safe)."""
    return {"status": "ok", "version": version("p42cloud")}


@router.get("/health/live")
@requires(PUBLIC)
async def live() -> dict[str, str]:
    """Whether the process answers at all, for a container's liveness check."""
    return {"status": "ok"}


@router.get("/health/ready")
@requires(PUBLIC)
async def ready(request: Request) -> dict[str, str]:
    """Whether it can serve: the database answers. 503 while it doesn't."""
    services: Services = request.app.state.services
    try:
        async with services.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as error:
        raise Problem(503, "not_ready", "The database doesn't answer.") from error
    return {"status": "ok", "version": version("p42cloud")}


@router.get("/me")
@requires(SIGNED_IN)
async def me(request: Request, caller: Who) -> dict[str, Any]:
    """The caller: their person record (what they may read of it) and their groups."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        person = records.to_json(
            records.catalog.tables["sys.person"], await records.read("sys.person", caller.person)
        )
    return {
        "person": person,
        "groups": sorted(str(g) for g in caller.access.groups),
        "channel": caller.channel,
        "verified_at": caller.verified_at.isoformat() if caller.verified_at else None,
        # The browser's own session, so a list of sessions can say which one this is.
        "session": str(caller.session) if caller.session else None,
    }


class UndoRequest(BaseModel):
    request: UUID | None = None
    entry: UUID | None = None


@router.post("/undo")
@requires(SIGNED_IN)
async def undo_changes(body: UndoRequest, request: Request, caller: Who) -> dict[str, Any]:
    """Undoes what one request did, or one audit entry, with the caller's rights today."""
    if (body.request is None) == (body.entry is None):
        raise Problem(422, "validation", "Name either a request or an entry.")
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        result = await undo(records.conn, records.catalog, request=body.request, entry=body.entry)
    return {
        "changed": [{"table": t, "record": str(r), "what": w} for t, r, w in result.changed],
        "skipped": [
            {"table": s.table, "record": str(s.record), "field": s.field, "reason": s.reason}
            for s in result.skipped
        ],
    }
