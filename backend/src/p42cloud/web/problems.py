"""Errors as RFC 9457 problem details, the same for every caller; internals never shown."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from p42cloud.kernel.errors import FieldError, Problem

TYPE = "application/problem+json"
log = logging.getLogger("p42cloud.web")


def answer(problem: Problem) -> JSONResponse:
    headers: dict[str, str] = {}
    retry = problem.extra.get("retry_after")
    if isinstance(retry, int):
        headers["Retry-After"] = str(retry)
    return JSONResponse(
        problem.to_dict(), status_code=problem.status, media_type=TYPE, headers=headers
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, problem: Problem) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        return answer(problem)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, error: RequestValidationError) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        errors = [
            FieldError(
                ".".join(str(p) for p in e.get("loc", ())[1:]) or "request",
                "invalid",
                str(e.get("msg", "")),
            )
            for e in error.errors()
        ]
        return answer(Problem(422, "validation", "Some fields aren't valid.", errors=errors))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, error: Exception) -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        log.exception("unexpected error on %s %s", request.method, request.url.path)
        return answer(Problem(500, "unexpected", "Something went wrong on our side. Try again."))
