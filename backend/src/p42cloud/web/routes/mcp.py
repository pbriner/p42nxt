"""The MCP server (Model Context Protocol, Streamable HTTP) for AI agents.

    POST /api/v1/mcp   one JSON-RPC message: initialize, tools/list, tools/call, ping

Agents use an API token (Authorization: Bearer), never a browser session: they get exactly what
the token allows. Answers are JSON; there is no event stream (GET answers 405). Every change an
agent makes is audited on the channel `mcp:token:<name>`.
"""

import json
from importlib.metadata import version
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from p42cloud.kernel.errors import Problem
from p42cloud.web.caller import Caller, check_origin, language, who
from p42cloud.web.mcp.tools import offered, tool_named
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["mcp"])
Who = Annotated[Caller, Depends(who)]

PROTOCOL = "2025-06-18"
MAX_BODY = 1_000_000
INSTRUCTIONS = (
    "Records of an installation's tables, as the token's grants allow. Start with list_tables and"
    " describe_table; filter with the filter language; changes need the record's _etag."
)
# JSON-RPC error codes.
PARSE_ERROR, INVALID_REQUEST, NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def _error(ident: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}
    )


def _result(ident: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": ident, "result": result})


def _problem(problem: Problem) -> dict[str, Any]:
    body: dict[str, Any] = {"code": problem.code, "title": problem.title}
    if problem.detail:
        body["detail"] = problem.detail
    if problem.errors:
        body["errors"] = [
            {"field": e.field, "code": e.code, "message": e.message} for e in problem.errors
        ]
    return body


async def _call(request: Request, caller: Caller, params: dict[str, Any]) -> dict[str, Any] | None:
    """A tool's answer as MCP content; None if there's no such tool."""
    tool = tool_named(str(params.get("name", "")))
    if tool is None:
        return None
    arguments = cast(dict[str, Any], params.get("arguments") or {})
    services: Services = request.app.state.services
    context = caller.context(language(request))
    try:
        async with services.records(context) as records:
            if tool.name not in {t["name"] for t in offered(records)}:
                raise Problem(403, "forbidden", "Your token doesn't allow this tool.")
            found = await tool.call(records, arguments)
    except Problem as problem:
        text = json.dumps(_problem(problem))
        return {"content": [{"type": "text", "text": text}], "isError": True}
    text = json.dumps(found, default=str)
    structured: dict[str, Any] = (
        cast(dict[str, Any], found) if isinstance(found, dict) else {"items": found}
    )
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured,
        "isError": False,
    }


def _parse(raw: bytes) -> dict[str, Any] | JSONResponse:
    """The message, or the JSON-RPC error saying what's wrong with it."""
    if len(raw) > MAX_BODY:
        return _error(None, INVALID_REQUEST, "The message is too large.")
    try:
        message: Any = json.loads(raw)
    except json.JSONDecodeError:
        return _error(None, PARSE_ERROR, "Not JSON.")
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "One JSON-RPC 2.0 message.")
    body = cast(dict[str, Any], message)
    if body.get("jsonrpc") != "2.0":
        return _error(None, INVALID_REQUEST, "One JSON-RPC 2.0 message.")
    return body


async def _answer(
    request: Request, agent: Caller, method: Any, ident: Any, params: dict[str, Any]
) -> JSONResponse:
    services: Services = request.app.state.services
    if method == "initialize":
        return _result(
            ident,
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "p42cloud", "version": version("p42cloud")},
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(ident, {})
    if method == "tools/list":
        async with services.records(agent.context(language(request))) as records:
            return _result(ident, {"tools": offered(records)})
    if method == "tools/call":
        answer = await _call(request, agent, params)
        if answer is None:
            return _error(ident, INVALID_PARAMS, f"No tool {params.get('name')!r}.")
        return _result(ident, answer)
    return _error(ident, NOT_FOUND, f"No method {method!r}.")


@router.post("/mcp")
@requires(SIGNED_IN)
async def mcp(request: Request, caller: Who) -> Response:
    """One JSON-RPC message from an agent: initialize, tools/list, tools/call or ping."""
    if not caller.channel.startswith("api:token:"):
        raise Problem(403, "tokens_only", "Agents connect with an API token.")
    services: Services = request.app.state.services
    check_origin(request, services.settings)  # a browser page elsewhere can't use it
    body = _parse(await request.body())
    if isinstance(body, JSONResponse):
        return body
    ident = body.get("id")
    if ident is None:
        return Response(status_code=202)  # a notification (initialized, cancelled …)
    # Agents act on their own channel, so the audit log tells them apart from scripts.
    agent = Caller(
        caller.person,
        caller.access,
        "mcp:" + caller.channel.removeprefix("api:"),
        caller.verified_at,
        caller.session,
        caller.zone,
    )
    params = cast(dict[str, Any], body.get("params") or {})
    return await _answer(request, agent, body.get("method"), ident, params)


@router.get("/mcp")
@requires(SIGNED_IN)
async def mcp_stream(caller: Who) -> Response:
    """No event stream: every answer comes with its request."""
    return Response(status_code=405, headers={"Allow": "POST"})
