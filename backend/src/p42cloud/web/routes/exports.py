"""A list as a file, with the same filters, sort and columns as the list itself.

GET /api/v1/<module>/<table>/export?format=xlsx&fields=number,partner.city&sort=-date&filter[…]

CSV, Excel (xlsx) or JSON; at most 50'000 records, only what the caller may read. Each export
is recorded in the audit log (who, which table, which filter, how many records).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from p42cloud.data.exporting import export
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.routes.records import condition_of
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["records"])
Who = Annotated[Caller, Depends(who)]


@router.get("/{module}/{table}/export")
@requires(SIGNED_IN)
async def export_records(
    module: str,
    table: str,
    request: Request,
    caller: Who,
    format: Annotated[str, Query(pattern=r"^(csv|xlsx|json)$")] = "csv",
    where: str | None = None,
    sort: str | None = None,
    fields: str | None = None,
) -> Response:
    """The records the caller may read, as a file to download."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        made = await export(
            records,
            f"{module}.{table}",
            file_format=format,
            where=condition_of(request, where),
            sort=[s for s in (sort or "").split(",") if s],
            fields=[f for f in (fields or "").split(",") if f],
        )
    headers: dict[str, Any] = {
        "Content-Disposition": f'attachment; filename="{made.filename}"',
        "X-Records": str(made.rows),
    }
    return Response(made.content, media_type=made.media_type, headers=headers)
