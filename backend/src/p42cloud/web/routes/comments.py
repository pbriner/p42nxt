"""Comments on records (`services/comments`):

    GET    /api/v1/<module>/<table>/<id>/comments   comments and work notes, newest first
    POST   /api/v1/<module>/<table>/<id>/comments   add one: {"text": "…", "kind": "comment"}
    DELETE /api/v1/comments/<id>                    remove one's own

Whoever may read the record reads its comments and may add one; work notes are read and added
only by those who may change it. Only the author removes either.
"""

from dataclasses import asdict
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request, Response

from p42cloud.services.comments import service as comments
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["comments"])
Who = Annotated[Caller, Depends(who)]


@router.delete("/comments/{comment_id}", status_code=204)
@requires(SIGNED_IN)
async def delete_comment(comment_id: UUID, request: Request, caller: Who) -> Response:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        await comments.remove(records, comment_id)
    return Response(status_code=204)


@router.get("/{module}/{table}/{sys_id}/comments")
@requires(SIGNED_IN)
async def list_comments(
    module: str, table: str, sys_id: UUID, request: Request, caller: Who
) -> dict[str, Any]:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        found = await comments.listed(records, f"{module}.{table}", sys_id)
    return {"comments": [asdict(c) for c in found]}


@router.post("/{module}/{table}/{sys_id}/comments", status_code=201)
@requires(SIGNED_IN)
async def add_comment(
    module: str,
    table: str,
    sys_id: UUID,
    request: Request,
    caller: Who,
    text: Annotated[str, Body(embed=True, max_length=10000)],
    kind: Annotated[Literal["comment", "work_note"], Body(embed=True)] = "comment",
) -> dict[str, Any]:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        made = await comments.add(records, f"{module}.{table}", sys_id, text, kind)
    return asdict(made)
