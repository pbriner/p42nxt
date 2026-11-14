"""Totals over a table, for widgets and charts: the same filters and access as lists.

GET /api/v1/<module>/<table>/aggregate?measure=sum&field=amount&group=state
GET /api/v1/<module>/<table>/aggregate?group=created&period=month&filter[state]=open
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from p42cloud.data.aggregates import MAX_GROUPS, aggregate
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.routes.records import condition_of
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["records"])
Who = Annotated[Caller, Depends(who)]


@router.get("/{module}/{table}/aggregate")
@requires(SIGNED_IN)
async def aggregate_records(
    module: str,
    table: str,
    request: Request,
    caller: Who,
    where: str | None = None,
    measure: str = "count",
    field: str | None = None,
    group: str | None = None,
    period: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_GROUPS)] = 10,
) -> dict[str, Any]:
    """A count (or sum, avg, min, max of `field`) over the records the caller may read,
    overall and per `group` (dates per `period`: day, week, month, year); `other` adds up the
    groups past `limit`. Groups carry their key and a label (choices and references named)."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        found = await aggregate(
            records,
            f"{module}.{table}",
            where=condition_of(request, where),
            measure=measure,
            field=field,
            group=group,
            period=period,
            limit=limit,
        )
    return {"total": found.total, "groups": found.groups, "other": found.other}
