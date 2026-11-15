"""Languages: which are offered, and the translations of what modules define, exported and
imported as JSON (`services/languages`). Administrators exchange translations; everyone, even
before signing in, may see which languages there are."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request
from sqlalchemy import text

from p42cloud.data.records import Records
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import Problem
from p42cloud.services.languages import service as languages
from p42cloud.web.caller import Caller, address, language, who
from p42cloud.web.permissions import PUBLIC, SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1/languages", tags=["languages"])
Who = Annotated[Caller, Depends(who)]
Code = Annotated[str, Path(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
MAX_BYTES = 5 * 1024 * 1024
TABLE = "sys.translation"


def _may(records: Records, *operations: str) -> None:
    """Translations are the administrators' (their grants on sys.translation)."""
    if not all(records.access.of(TABLE, op) for op in operations):
        raise Problem(403, "forbidden", "Only administrators exchange translations.")


@router.get("")
@requires(PUBLIC)
async def get_languages(request: Request) -> dict[str, Any]:
    """The languages, which are offered, and how much of what modules define each covers."""
    services: Services = request.app.state.services
    services.rates.take(f"address:{address(request)}")
    with working_as(Context(channel="ui", access=EVERYTHING)):
        async with transaction(services.engine, "sys") as conn:
            found = await conn.execute(
                text(
                    "SELECT code, name, offered FROM sys.language WHERE deleted IS NULL"
                    " ORDER BY code"
                )
            )
            rows = [dict(r._mapping) for r in found]  # pyright: ignore[reportPrivateUsage]
    return {"languages": rows}


@router.get("/{code}/export")
@requires(SIGNED_IN)
async def export_language(
    code: Code,
    request: Request,
    caller: Who,
    module: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9]{1,15}$")] = None,
) -> dict[str, Any]:
    """Every text modules define, in English, with its translation and state (missing, done,
    outdated): the file to fill in and import again."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        _may(records, "read")
        return await languages.export(
            records.conn, records.catalog, languages.check_language(code), module
        )


@router.post("/{code}/import")
@requires(SIGNED_IN)
async def import_language(
    code: Code, given: Annotated[dict[str, Any], Body()], request: Request, caller: Who
) -> dict[str, Any]:
    """This installation's translations from an export file, all or nothing; an empty text
    removes one. Shown at once, and kept over module updates."""
    if int(request.headers.get("content-length") or 0) > MAX_BYTES:
        raise Problem(413, "too_large", "At most 5 MB at once.")
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        _may(records, "create", "write")
        counts = await languages.import_texts(
            records.conn, records.catalog, languages.check_language(code), given
        )
    # The records modules wrote are the platform's: it puts the translations on them.
    with working_as(Context(channel="ui", access=EVERYTHING)):
        async with transaction(services.engine, "sys") as conn:
            counts["shown"] = await languages.apply(conn, await services.cache.catalog())
    return counts
