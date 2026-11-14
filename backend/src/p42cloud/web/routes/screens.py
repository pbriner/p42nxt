"""What the screens read besides records: the navigator, the theme, the search across lists.

Any client may use them; the browser app has nothing the API doesn't offer to everyone.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request

from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.ui.navigation.service import navigation
from p42cloud.ui.search.service import scopes, search
from p42cloud.ui.themes.service import theme
from p42cloud.ui.widgets.service import dashboard
from p42cloud.web.caller import Caller, address, language, who
from p42cloud.web.permissions import PUBLIC, SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["screens"])
Who = Annotated[Caller, Depends(who)]


@router.get("/navigation")
@requires(SIGNED_IN)
async def get_navigation(request: Request, caller: Who) -> dict[str, Any]:
    """The navigator: categories, modules, sections and the items the caller may open."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        return await navigation(records)


@router.get("/theme")
@requires(PUBLIC)
async def get_theme(
    request: Request, name: Annotated[str | None, Query(max_length=40)] = None
) -> dict[str, Any]:
    """A theme's colours and look: the one named, else the installation's default.

    Public, so the sign-in page looks like the app; themes hold nothing secret.
    """
    services: Services = request.app.state.services
    services.rates.take(f"address:{address(request)}")
    with working_as(Context(channel="ui")):
        async with transaction(services.engine, "sys") as conn:
            return await theme(conn, name, language(request))


@router.get("/dashboards/{name}")
@requires(SIGNED_IN)
async def get_dashboard(
    name: Annotated[str, Path(pattern=r"^[a-z][a-z0-9]{1,15}$")], request: Request, caller: Who
) -> dict[str, Any]:
    """A dashboard (`home`, or a module's ID) for the caller: the widgets they may see, how
    strongly each is offered, their arrangement (their own, else the defaults), and which
    widgets are new to them. Their arrangement is a record of `sys.dashboard_layout`."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        return await dashboard(records, name)


@router.get("/search/scopes")
@requires(SIGNED_IN)
async def get_search_scopes(request: Request, caller: Who) -> dict[str, Any]:
    """The lists the caller may search in."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        found = await scopes(records)
    return {
        "scopes": [
            {
                "list": s.name,
                "label": s.label,
                "module": s.module,
                "module_label": s.module_label,
                "table": s.table.key,
            }
            for s in found
        ]
    }


@router.get("/search")
@requires(SIGNED_IN)
async def get_search(
    request: Request,
    caller: Who,
    q: Annotated[str, Query(max_length=100)],
    scope: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    """Records matching `q` in every list the caller may search (or in `scope`), a group per
    list with its total. Access applies as in the lists themselves."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        return await search(records, q, scope=scope, per_list=limit)
