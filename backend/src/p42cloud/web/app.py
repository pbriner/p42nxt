"""The web app: the HTTP API (and, later, the browser app).

At start it refuses an unsafe database, brings the database and the modules to this version (as
the owner, so audit months never run out), connects the hooks and starts the catalog cache.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.metadata import version

import httpx
from fastapi import FastAPI, Request, Response

from p42cloud.data.catalog import MetadataCache
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.safety import problems
from p42cloud.kernel.secrets import Sealer
from p42cloud.modules.core import CORE
from p42cloud.modules.install import compose, install_modules
from p42cloud.modules.registry import discover
from p42cloud.security.providers.service import Providers
from p42cloud.security.sign_in.sessions import SignIn
from p42cloud.web import problems as problem_answers
from p42cloud.web.caller import Rates
from p42cloud.web.routes import meta, people, records, sign_in, tables
from p42cloud.web.services import Services


def create_app(
    settings: Settings,
    *,
    sealer: Sealer | None = None,
    http: httpx.AsyncClient | None = None,
    upgrade: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_engine(settings.database_url)
        found = await problems(engine)
        if found:
            await engine.dispose()
            raise RuntimeError("The database isn't safe: " + " ".join(found))
        modules = [CORE, *discover()]
        owner = create_engine(settings.owner_database_url)
        if upgrade:
            with working_as(Context(channel="upgrade")):
                await install_modules(owner, modules)
        else:
            compose(modules)
        client = http or httpx.AsyncClient(timeout=15)
        seal = sealer or Sealer.from_file(settings.master_key_file)
        signing_in = SignIn(engine, seal, settings)
        providers = (
            Providers(engine, seal, settings=settings, http=client, sign_in=signing_in)
            if settings.public_url
            else None
        )
        cache = MetadataCache(engine)
        await cache.start()
        app.state.services = Services(
            settings,
            engine,
            owner,
            seal,
            signing_in,
            providers,
            cache,
            Rates(settings.requests_per_minute),
            client,
        )
        try:
            yield
        finally:
            await cache.stop()
            if http is None:
                await client.aclose()
            await engine.dispose()
            await owner.dispose()

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
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(meta.router)
    app.include_router(sign_in.router)
    app.include_router(people.router)
    app.include_router(tables.router)
    app.include_router(records.router)  # last: its paths take any module and table
    return app
