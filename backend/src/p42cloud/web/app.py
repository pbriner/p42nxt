"""The web app: the HTTP API and the browser app.

At start it refuses an unsafe database, brings the database and the modules to this version (as
the owner, so audit months never run out), connects the hooks and starts the catalog cache.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI, Request, Response
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data import installation
from p42cloud.data.catalog import MetadataCache
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.outgoing import safe_client
from p42cloud.kernel.safety import problems
from p42cloud.kernel.secrets import Sealer, use_sealer
from p42cloud.modules.core import CORE
from p42cloud.modules.install import compose, install_modules
from p42cloud.modules.registry import discover
from p42cloud.security.providers.service import Providers
from p42cloud.security.sign_in.sessions import SignIn
from p42cloud.web import frontend, module_routes
from p42cloud.web import problems as problem_answers
from p42cloud.web.caller import Rates
from p42cloud.web.routes import (
    actions,
    aggregates,
    comments,
    files,
    languages,
    mcp,
    meta,
    people,
    records,
    screens,
    setup,
    sign_in,
    tables,
)
from p42cloud.web.services import Services

log = logging.getLogger("p42cloud.web")


async def _start(
    settings: Settings, *, sealer: Sealer | None, http: httpx.AsyncClient | None, upgrade: bool
) -> Services:
    """What the routes share: checked database, modules installed or composed, sign-in."""
    engine = create_engine(settings.database_url)
    found = await problems(engine)
    if found:
        await engine.dispose()
        raise RuntimeError("The database isn't safe: " + " ".join(found))
    modules = [CORE, *discover(settings.modules_dir)]
    owner = create_engine(settings.owner_database_url)
    if upgrade:
        with working_as(Context(channel="upgrade")):
            await install_modules(owner, modules)
    else:
        compose(modules)
    # Every outside call goes through the safe client (E-10).
    client = http or safe_client()
    seal = sealer or Sealer.from_file(settings.master_key_file)
    use_sealer(seal)
    signing_in = SignIn(engine, seal, settings)
    await installation.refresh(engine)
    providers = Providers(engine, seal, settings=settings, http=client, sign_in=signing_in)
    cache = MetadataCache(engine)
    await cache.start()
    return Services(
        settings,
        engine,
        owner,
        seal,
        signing_in,
        providers,
        cache,
        Rates(lambda: installation.requests_per_minute(settings)),
        client,
    )


async def _keep_settings(engine: AsyncEngine) -> None:
    """The settings administrators change in the app, read again every half minute."""
    while True:
        await asyncio.sleep(installation.REFRESH)
        try:
            await installation.refresh(engine)
        except Exception:
            log.warning("The settings couldn't be read again; the last ones stay.")


def create_app(
    settings: Settings,
    *,
    sealer: Sealer | None = None,
    http: httpx.AsyncClient | None = None,
    upgrade: bool = True,
    frontend_dir: Path | None = frontend.BUILT,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        services = await _start(settings, sealer=sealer, http=http, upgrade=upgrade)
        app.state.services = services
        module_routes.mount(modules_router)  # the modules are composed by now
        refreshing = asyncio.create_task(_keep_settings(services.engine))
        try:
            yield
        finally:
            refreshing.cancel()
            await services.cache.stop()
            if http is None:
                await services.http.aclose()
            await services.engine.dispose()
            await services.owner.dispose()

    app = FastAPI(
        title="P42cloud",
        version=version("p42cloud"),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url=None,
        redoc_url=None,
    )
    problem_answers.install(app)

    @app.middleware("http")
    async def protective_headers(  # pyright: ignore[reportUnusedFunction]
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # The app's page sets its own, less strict policy; everything else may load nothing.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(meta.router)
    app.include_router(sign_in.router)
    app.include_router(setup.router)
    app.include_router(people.router)
    app.include_router(tables.router)
    app.include_router(screens.router)
    app.include_router(languages.router)
    app.include_router(mcp.router)
    # The modules' own routes, filled at start: before the generic ones they'd otherwise match.
    modules_router = APIRouter()
    app.include_router(modules_router)
    app.include_router(actions.router)
    # Before the record routes: `aggregate` would otherwise be taken for a record's ID.
    app.include_router(aggregates.router)
    app.include_router(comments.router)
    app.include_router(files.router)  # before the records': its paths are more particular
    app.include_router(records.router)  # its paths take any module and table
    frontend.install(app, frontend_dir)  # last: it answers every other address
    return app
