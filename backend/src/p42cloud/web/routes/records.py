"""The generic record routes: the same for every table of every module.

    GET    /api/v1/<module>/<table>                 a page of records
    POST   /api/v1/<module>/<table>                 a new record
    GET    /api/v1/<module>/<table>/<id>            one record (its version in ETag)
    PATCH  /api/v1/<module>/<table>/<id>            change fields (needs If-Match)
    GET    /api/v1/<module>/<table>/<id>/deletion   what deleting it would do to other records
    DELETE /api/v1/<module>/<table>/<id>            soft deletion (needs If-Match; `confirm`)
    POST   /api/v1/<module>/<table>/<id>/restore    restore

Filters come as `filter[field][op]=value` (all ANDed; lists comma-separated) and as
`where=<JSON>` for anything with OR or NOT; both together are ANDed. Lists and single records
carry `_display` (references and choices named), `_access` (what the caller may do with the
record) and `_actions` (the UI actions they may run on it); `fields=a,b` keeps only those
fields, and `fields=partner.city` adds a field of a referenced record (sortable by too).
"""

import json
import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response

from p42cloud.data.history import MAX_ENTRIES, history
from p42cloud.data.presenting import annotate, deletion_preview
from p42cloud.data.read_log import log_reads
from p42cloud.kernel.errors import Problem
from p42cloud.ui.actions.service import available
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["records"])
Who = Annotated[Caller, Depends(who)]
_FILTER = re.compile(r"^filter\[([a-z0-9_.]+)\](?:\[([a-z_]+)\])?$")
_LISTS = frozenset({"in", "not_in", "between"})
_NO_VALUE = frozenset({"empty", "not_empty"})


def condition_of(request: Request, where: str | None) -> Any:
    """The URL's filters as one condition in the filter language."""
    parts: list[Any] = []
    for key, value in request.query_params.multi_items():
        found = _FILTER.match(key)
        if found is None:
            continue
        field, op = found[1], found[2] or "eq"
        leaf: dict[str, Any] = {"field": field, "op": op}
        if op not in _NO_VALUE:
            leaf["value"] = value.split(",") if op in _LISTS else value
        parts.append(leaf)
    if where:
        try:
            parts.append(json.loads(where))
        except json.JSONDecodeError as error:
            raise Problem(
                400, "bad_where", "`where` must be JSON in the filter language."
            ) from error
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"and": parts}


_ALWAYS = frozenset({"sys_id", "_etag", "_display", "_access", "_actions"})


def _only(body: dict[str, Any], fields: str | None) -> dict[str, Any]:
    if not fields:
        return body
    wanted = {f for f in fields.split(",") if f} | _ALWAYS
    kept = {k: v for k, v in body.items() if k in wanted}
    kept["_display"] = {k: v for k, v in body.get("_display", {}).items() if k in wanted}
    return kept


def _key(module: str, table: str) -> str:
    return f"{module}.{table}"


def _one(response: Response, body: dict[str, Any]) -> dict[str, Any]:
    response.headers["ETag"] = body["_etag"]
    return body


@router.get("/{module}/{table}")
@requires(SIGNED_IN)
async def list_records(
    module: str,
    table: str,
    request: Request,
    caller: Who,
    where: str | None = None,
    sort: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    after: str | None = None,
    count: bool = True,
    deleted: bool = False,
    fields: str | None = None,
) -> dict[str, Any]:
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        page = await records.list(
            key,
            where=condition_of(request, where),
            sort=[s for s in (sort or "").split(",") if s],
            limit=limit,
            after=after,
            count=count,
            deleted=deleted,
            columns=[f for f in (fields or "").split(",") if "." in f],
        )
        found = await annotate(records, records.catalog.tables[key], page.records)
        await available(records, records.catalog.tables[key], found)
        await log_reads(records, records.catalog.tables[key], (r.sys_id for r in page.records))
    return {
        "records": [_only(r, fields) for r in found],
        "next": page.next,
        "total": page.total,
        "total_exact": page.total_exact,
    }


@router.post("/{module}/{table}", status_code=201)
@requires(SIGNED_IN)
async def create_record(
    module: str,
    table: str,
    values: Annotated[dict[str, Any], Body()],
    request: Request,
    response: Response,
    caller: Who,
) -> dict[str, Any]:
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        made = await records.create(key, values)
        body = records.to_json(records.catalog.tables[key], made)
    response.headers["Location"] = f"/api/v1/{module}/{table}/{made.sys_id}"
    return _one(response, body) if body.get("_etag") not in (None, '""') else body


@router.get("/{module}/{table}/{sys_id}")
@requires(SIGNED_IN)
async def read_record(
    module: str,
    table: str,
    sys_id: UUID,
    request: Request,
    response: Response,
    caller: Who,
    fields: str | None = None,
) -> dict[str, Any]:
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        record = await records.read(key, sys_id)
        (found,) = await annotate(records, records.catalog.tables[key], [record])
        await available(records, records.catalog.tables[key], [found])
        await log_reads(records, records.catalog.tables[key], [record.sys_id])
    return _one(response, _only(found, fields))


@router.get("/{module}/{table}/{sys_id}/history")
@requires(SIGNED_IN)
async def record_history(
    module: str,
    table: str,
    sys_id: UUID,
    request: Request,
    caller: Who,
    limit: Annotated[int, Query(ge=1, le=MAX_ENTRIES)] = 100,
) -> dict[str, Any]:
    """The record's changes from the audit log, newest first, for those who may read it: of each
    change only the fields they may read."""
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        return {"entries": await history(records, key, sys_id, limit)}


@router.patch("/{module}/{table}/{sys_id}")
@requires(SIGNED_IN)
async def change_record(
    module: str,
    table: str,
    sys_id: UUID,
    values: Annotated[dict[str, Any], Body()],
    request: Request,
    response: Response,
    caller: Who,
    if_match: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        changed = await records.update(key, sys_id, values, if_match)
        found = records.to_json(records.catalog.tables[key], changed)
    return _one(response, found)


@router.get("/{module}/{table}/{sys_id}/deletion")
@requires(SIGNED_IN)
async def record_deletion(
    module: str, table: str, sys_id: UUID, request: Request, caller: Who
) -> dict[str, Any]:
    """What deleting the record would do, changing nothing: the records deleted with it
    (`cascade`), emptied (`clear`), refusing it (`blocked`) or kept pointing to it (`kept`).
    A delete that changes other records needs this `fingerprint` as `confirm`."""
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        await records.read(key, sys_id)
        return await deletion_preview(records, key, sys_id)


@router.delete("/{module}/{table}/{sys_id}")
@requires(SIGNED_IN)
async def delete_record(
    module: str,
    table: str,
    sys_id: UUID,
    request: Request,
    response: Response,
    caller: Who,
    if_match: Annotated[str | None, Header()] = None,
    confirm: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    """Refused (409 `confirm`) when it would change other records and `confirm` isn't the
    fingerprint of its deletion preview, and (409 `in_use`) while records it refuses point to it."""
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        gone = await records.delete(key, sys_id, if_match, confirm or "")
        found = records.to_json(records.catalog.tables[key], gone)
    return _one(response, found)


@router.post("/{module}/{table}/{sys_id}/restore")
@requires(SIGNED_IN)
async def restore_record(
    module: str, table: str, sys_id: UUID, request: Request, response: Response, caller: Who
) -> dict[str, Any]:
    services: Services = request.app.state.services
    key = _key(module, table)
    async with services.records(caller.context(language(request))) as records:
        back = records.to_json(records.catalog.tables[key], await records.restore(key, sys_id))
    return _one(response, back)
