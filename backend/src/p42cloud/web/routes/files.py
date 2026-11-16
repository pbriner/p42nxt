"""Files attached to records (`services/files`):

    GET    /api/v1/<module>/<table>/<id>/files     the record's files
    POST   /api/v1/<module>/<table>/<id>/files?name=<file name>   attach one (the body is the file)
    GET    /api/v1/files/<id>                      its content (`?inline=true`: shown, if it can be)
    DELETE /api/v1/files/<id>                      remove it from its record

Rights are the record's: reading it lets one read its files, changing it lets one attach and
remove them. The body is read only up to the size limit.
"""

from dataclasses import asdict
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from p42cloud.kernel.errors import Problem
from p42cloud.services.files import service as files
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["files"])
Who = Annotated[Caller, Depends(who)]


@router.get("/files/{file_id}")
@requires(SIGNED_IN)
async def get_file(
    file_id: UUID,
    request: Request,
    caller: Who,
    inline: Annotated[bool, Query()] = False,
) -> Response:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        found, data = await files.read(records, file_id)
    shown = inline and found.type in files.SHOWN_INLINE
    disposition = "inline" if shown else "attachment"
    return Response(
        content=data,
        media_type=found.type,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(found.name)}",
            "X-Content-Type-Options": "nosniff",
            # Whatever the file holds, it runs nothing and loads nothing.
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/files/{file_id}", status_code=204)
@requires(SIGNED_IN)
async def delete_file(file_id: UUID, request: Request, caller: Who) -> Response:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        await files.remove(records, file_id)
    return Response(status_code=204)


@router.get("/{module}/{table}/{sys_id}/files")
@requires(SIGNED_IN)
async def list_files(
    module: str, table: str, sys_id: UUID, request: Request, caller: Who
) -> dict[str, Any]:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        found = await files.listed(records, f"{module}.{table}", sys_id)
    return {"files": [asdict(f) for f in found]}


@router.post("/{module}/{table}/{sys_id}/files", status_code=201)
@requires(SIGNED_IN)
async def attach_file(
    module: str,
    table: str,
    sys_id: UUID,
    request: Request,
    caller: Who,
    name: Annotated[str, Query(min_length=1, max_length=400)],
) -> dict[str, Any]:
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        largest = await files.limit(records)
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > largest:
                raise Problem(413, "too_large", f"Files are at most {largest // (1024 * 1024)} MB.")
        stored = await files.attach(records, f"{module}.{table}", sys_id, name, bytes(data))
    return asdict(stored)
