"""The MCP server: tools generated from a token's grants, run with the API's rules."""

import json
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest

from p42cloud.kernel.config import Settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.tokens.service import make_token
from p42cloud.web.app import create_app
from tests.security.helpers import acting

SITE = "https://p42.test"


@pytest.fixture(scope="module")
async def tokens(upgraded: Settings) -> dict[str, str]:
    engine = create_engine(upgraded.database_url)
    try:
        async with acting(engine, None) as records:
            tag = uuid.uuid4().hex[:6]
            admin = (await records.create("sys.person", {"name": f"Agent admin {tag}"})).sys_id
            await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin})
            plain = (await records.create("sys.person", {"name": f"Agent plain {tag}"})).sys_id
            return {
                "admin": (await make_token(records.conn, admin, "Agent")).token,
                "plain": (await make_token(records.conn, plain, "Agent")).token,
            }
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
async def client(upgraded: Settings) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(
        upgraded.model_copy(update={"public_url": SITE}),
        sealer=Sealer(os.urandom(32)),
        upgrade=False,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=SITE) as made:
            yield made


async def _rpc(
    client: httpx.AsyncClient, token: str, method: str, params: Any = None, ident: Any = 1
) -> Any:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if ident is not None:
        message["id"] = ident
    if params is not None:
        message["params"] = params
    return await client.post(
        "/api/v1/mcp", json=message, headers={"Authorization": f"Bearer {token}"}
    )


async def test_an_agent_gets_the_tools_its_token_allows(
    client: httpx.AsyncClient, tokens: dict[str, str]
) -> None:
    hello = (
        await _rpc(client, tokens["plain"], "initialize", {"protocolVersion": "2025-06-18"})
    ).json()
    plain = {
        t["name"]: t
        for t in (await _rpc(client, tokens["plain"], "tools/list")).json()["result"]["tools"]
    }
    admin = {
        t["name"]: t
        for t in (await _rpc(client, tokens["admin"], "tools/list")).json()["result"]["tools"]
    }
    assert hello["result"]["protocolVersion"] == "2025-06-18"
    readable = plain["list_records"]["inputSchema"]["properties"]["table"]["enum"]
    assert "sys.person" in readable and "sys.grant" not in readable
    assert "sys.person" not in plain["create_record"]["inputSchema"]["properties"]["table"]["enum"]
    assert "sys.person" in admin["create_record"]["inputSchema"]["properties"]["table"]["enum"]
    assert plain["delete_record"]["annotations"]["destructiveHint"] is True


async def test_an_agents_changes_are_its_own_and_checked(
    client: httpx.AsyncClient, tokens: dict[str, str]
) -> None:
    made = await _rpc(
        client,
        tokens["admin"],
        "tools/call",
        {
            "name": "create_record",
            "arguments": {"table": "sys.person", "values": {"name": "Made by an agent"}},
        },
    )
    refused = await _rpc(
        client,
        tokens["plain"],
        "tools/call",
        {"name": "list_records", "arguments": {"table": "sys.grant"}},
    )
    result = made.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["source"] == "mcp:token:Agent"  # audited as the agent
    assert refused.json()["result"]["isError"] is True
    assert json.loads(refused.json()["result"]["content"][0]["text"])["code"] == "no_table"


async def test_the_protocol_answers_what_it_should(
    client: httpx.AsyncClient, tokens: dict[str, str]
) -> None:
    note = await _rpc(client, tokens["plain"], "notifications/initialized", ident=None)
    unknown = await _rpc(client, tokens["plain"], "resources/list")
    no_tool = await _rpc(client, tokens["plain"], "tools/call", {"name": "format_disk"})
    broken = await client.post(
        "/api/v1/mcp", content=b"{not json", headers={"Authorization": f"Bearer {tokens['plain']}"}
    )
    anonymous = await client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert note.status_code == 202
    assert unknown.json()["error"]["code"] == -32601
    assert no_tool.json()["error"]["code"] == -32602
    assert broken.json()["error"]["code"] == -32700
    assert anonymous.status_code == 401
