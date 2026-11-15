"""Signing in and out over HTTP: the browser's side of the sign-in service.

The session's token lives in an HttpOnly cookie (SameSite=Lax, Secure except on plain-http
development). When signed in, a readable CSRF cookie comes with it: the browser app sends its
value back in the `X-CSRF-Token` header with every change. Changes with cookies must also come
from this installation (Origin).
"""

import secrets
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from p42cloud.data import installation
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.errors import Problem
from p42cloud.security.sign_in.sessions import Started
from p42cloud.web.caller import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Caller,
    address,
    check_origin,
    who,
)
from p42cloud.web.permissions import PUBLIC, SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1/sign-in", tags=["sign-in"])
Who = Annotated[Caller, Depends(who)]


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    services.rates.take(f"sign-in:{address(request)}")
    check_origin(request, services.settings)
    return services


def _secure(services: Services) -> bool:
    url = installation.public_url(services.settings) or ""
    return url.startswith("https://") or services.settings.environment == "production"


def set_session(response: Response, services: Services, started: Started) -> dict[str, Any]:
    """The session cookie, and with a signed-in session the CSRF cookie."""
    secure = _secure(services)
    response.set_cookie(
        SESSION_COOKIE, started.token, httponly=True, secure=secure, samesite="lax", path="/"
    )
    if started.stage == "active":
        response.set_cookie(
            CSRF_COOKIE,
            secrets.token_urlsafe(32),
            httponly=False,
            secure=secure,
            samesite="lax",
            path="/",
        )
    return {"stage": started.stage}


def _token(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise Problem(401, "not_signed_in", "Start signing in again.")
    return token


class Password(BaseModel):
    email: str
    password: str


class Code(BaseModel):
    code: str


class Passkey(BaseModel):
    answer: dict[str, Any]
    label: str = "Passkey"


class Accept(BaseModel):
    token: str
    password: str


class NewApp(BaseModel):
    label: str = "Authenticator app"


class ConfirmApp(BaseModel):
    credential: UUID
    code: str


@router.post("")
@requires(PUBLIC)
async def start(body: Password, request: Request, response: Response) -> dict[str, Any]:
    """Email and password; then the second factor (or setting one up)."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        started = await services.sign_in.start(
            body.email, body.password, address(request), request.headers.get("user-agent")
        )
    return set_session(response, services, started)


@router.post("/invitation")
@requires(PUBLIC)
async def accept_invitation(body: Accept, request: Request, response: Response) -> dict[str, Any]:
    """An invitation's token and a new password; then the second factor is set up."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        started = await services.sign_in.accept_invitation(
            body.token, body.password, address(request), request.headers.get("user-agent")
        )
    return set_session(response, services, started)


@router.post("/code")
@requires(PUBLIC)
async def with_code(body: Code, request: Request, response: Response) -> dict[str, Any]:
    """An authenticator code or a recovery code: signed in, with a new session."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        started = await services.sign_in.finish(
            _token(request), body.code, address(request), request.headers.get("user-agent")
        )
    return set_session(response, services, started)


@router.post("/passkey/challenge")
@requires(PUBLIC)
async def passkey_challenge(request: Request) -> dict[str, Any]:
    """What the browser needs to sign in with a passkey."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        return await services.sign_in.passkey_challenge(_token(request))


@router.post("/passkey")
@requires(PUBLIC)
async def with_passkey(body: Passkey, request: Request, response: Response) -> dict[str, Any]:
    services = _services(request)
    with working_as(Context(channel="ui")):
        started = await services.sign_in.finish(
            _token(request), body.answer, address(request), request.headers.get("user-agent")
        )
    return set_session(response, services, started)


@router.post("/setup/app")
@requires(PUBLIC)
async def setup_app(body: NewApp, request: Request) -> dict[str, Any]:
    """A first second factor: an authenticator app's key, shown once."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        made = await services.sign_in.enroll(_token(request), body.label)
    return {"credential": str(made.credential), "key": made.key, "link": made.link}


@router.post("/setup/app/confirm")
@requires(PUBLIC)
async def confirm_app(body: ConfirmApp, request: Request, response: Response) -> dict[str, Any]:
    """The app's first code: signed in, and the recovery codes, shown once."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        started, codes = await services.sign_in.finish_enrolling(
            _token(request), body.credential, body.code, address(request)
        )
    return set_session(response, services, started) | {"recovery_codes": codes}


@router.post("/setup/passkey/options")
@requires(PUBLIC)
async def setup_passkey_options(request: Request) -> dict[str, Any]:
    services = _services(request)
    with working_as(Context(channel="ui")):
        return await services.sign_in.passkey_registration(_token(request))


@router.post("/setup/passkey")
@requires(PUBLIC)
async def setup_passkey(body: Passkey, request: Request, response: Response) -> dict[str, Any]:
    """A passkey as the first second factor: signed in, and the recovery codes, shown once."""
    services = _services(request)
    with working_as(Context(channel="ui")):
        started, codes = await services.sign_in.enroll_with_passkey(
            _token(request), body.answer, body.label, address(request)
        )
    return set_session(response, services, started) | {"recovery_codes": codes}


@router.post("/verify")
@requires(SIGNED_IN)
async def verify_again(body: Code, request: Request, caller: Who) -> dict[str, Any]:
    """The second factor again, for changes that need it to be recent."""
    services = _services(request)
    if caller.session is None:
        raise Problem(409, "no_session", "API tokens can't give a second factor.")
    with working_as(Context(channel="ui")):
        session = await services.sign_in.verify_again(_token(request), body.code)
    return {"verified_at": session.verified.isoformat() if session.verified else None}


@router.post("/out")
@requires(SIGNED_IN)
async def sign_out(request: Request, response: Response, caller: Who) -> dict[str, str]:
    services = _services(request)
    if caller.session is not None:
        with working_as(Context(channel="ui")):
            await services.sign_in.sign_out(_token(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"stage": "signed_out"}


@router.get("/providers")
@requires(PUBLIC)
async def providers(request: Request) -> list[dict[str, str]]:
    """The sign-in providers to offer on the sign-in page."""
    services = _services(request)
    if services.providers is None:
        return []
    with working_as(Context(channel="ui")):
        found = await services.providers.available()
    return [{"id": str(i), "name": n} for i, n in found]


@router.get("/provider/{provider}")
@requires(PUBLIC)
async def provider(provider: UUID, request: Request) -> RedirectResponse:
    """Off to the provider to sign in there."""
    services = _services(request)
    if services.providers is None:
        raise Problem(409, "providers_off", "Sign-in providers need the installation's address.")
    with working_as(Context(channel="ui")):
        redirect = await services.providers.begin(provider)
    return RedirectResponse(redirect.url, status_code=302)


@router.get("/provider-callback")
@requires(PUBLIC)
async def provider_callback(state: str, code: str, request: Request) -> RedirectResponse:
    """Back from the provider: signed in, and on to the app."""
    services = _services(request)
    if services.providers is None:
        raise Problem(409, "providers_off", "Sign-in providers need the installation's address.")
    with working_as(Context(channel="ui")):
        started = await services.providers.complete(
            state, code, address(request), request.headers.get("user-agent")
        )
    answer = RedirectResponse("/", status_code=302)
    set_session(answer, services, started)
    return answer
