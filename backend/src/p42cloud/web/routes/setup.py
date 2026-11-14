"""The installation itself, and the web installer.

    GET  /api/v1/installation      its name and language, and whether setup is still open
    GET  /api/v1/setup/modules     while open: the installed modules, to keep or switch off
    POST /api/v1/setup             while open, with the setup code: the first administrator

After setting up, the administrator is signed in up to the second factor, which they set up next.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from p42cloud.data import settings
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules.setup import Setup, is_open, optional_modules, set_up
from p42cloud.security.sign_in.events import event, failures
from p42cloud.web.caller import address, check_origin
from p42cloud.web.permissions import PUBLIC, requires
from p42cloud.web.routes.sign_in import set_session
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["setup"])
WRONG_CODES = 20  # per address and hour: after that, the installer waits
HOUR = timedelta(hours=1)


class SetupBody(BaseModel):
    code: Annotated[str, Field(max_length=40)]
    language: Annotated[str, Field(pattern=r"^(en|de)$")]
    name: Annotated[str, Field(min_length=1, max_length=40)]
    admin_name: Annotated[str, Field(min_length=1, max_length=120)]
    email: Annotated[str, Field(max_length=254)]
    password: Annotated[str, Field(max_length=200)]
    modules: list[Annotated[str, Field(max_length=16)]] = Field(
        default_factory=list[str], max_length=100
    )
    backups: bool
    master_key: bool


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    services.rates.take(f"setup:{address(request)}")
    return services


@router.get("/installation")
@requires(PUBLIC)
async def installation(request: Request) -> dict[str, Any]:
    """The installation's name and language, and whether the web installer is still open."""
    services = _services(request)
    with working_as(Context(channel="ui", access=EVERYTHING)):
        async with transaction(services.engine, "sys") as conn:
            name = await settings.value(conn, "sys.app_name")
            language = await settings.value(conn, "sys.language")
    return {
        "name": name,
        "language": language,
        "setup_open": bool(services.settings.setup_code) and await is_open(services.engine),
    }


@router.get("/setup/modules")
@requires(PUBLIC)
async def setup_modules(request: Request) -> dict[str, Any]:
    """While the installer is open: the installed modules, and which are part of the core."""
    services = _services(request)
    if not services.settings.setup_code or not await is_open(services.engine):
        raise Problem(409, "set_up", "This installation is set up already. Sign in.")
    return {"modules": await optional_modules(services.engine)}


@router.post("/setup")
@requires(PUBLIC)
async def setup(body: SetupBody, request: Request, response: Response) -> dict[str, Any]:
    """Sets the installation up: its first administrator, name, language and modules."""
    services = _services(request)
    check_origin(request, services.settings)
    where = address(request) or "unknown"
    with working_as(Context(channel="setup", access=EVERYTHING)):
        async with transaction(services.engine, "sys") as conn:
            if (
                await failures(
                    conn, "setup_code_failed", "address", where, datetime.now(UTC) - HOUR
                )
                >= WRONG_CODES
            ):
                raise Problem(429, "slow_down", "Too many wrong setup codes; try again in an hour.")
    given = Setup(
        code=body.code,
        language=body.language,
        name=body.name,
        admin_name=body.admin_name,
        email=body.email.strip().lower(),
        password=body.password,
        modules=frozenset(body.modules),
        backups=body.backups,
        master_key=body.master_key,
    )
    try:
        await set_up(services.engine, services.owner, services.settings.setup_code, given)
    except Problem as problem:
        if problem.code == "wrong_setup_code":
            with working_as(Context(channel="setup", access=EVERYTHING)):
                async with transaction(services.engine, "sys") as conn:
                    await event(conn, "setup_code_failed", address=where)
        raise
    with working_as(Context(channel="ui")):
        started = await services.sign_in.start(
            given.email, given.password, address(request), request.headers.get("user-agent")
        )
    return set_session(response, services, started)
