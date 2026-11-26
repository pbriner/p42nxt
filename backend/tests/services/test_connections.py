"""Outside calls: the safe client, connection types, each person's connections and the vault."""

import json
import os
import uuid
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel import outgoing
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.outgoing import Refused, host_allowed, safe_client
from p42cloud.kernel.secrets import Sealer, use_sealer
from p42cloud.services.connections.models import ConnectionType
from p42cloud.services.connections.service import (
    TEST_TRANSPORTS,
    TYPES,
    check,
    connect,
    write_types,
)
from p42cloud.services.jobs.service import run_job
from tests.security.helpers import acting

HOST = "1.1.1.1"  # a public address: no name to resolve in tests
SEEN: list[httpx.Request] = []


def _answer(request: httpx.Request) -> httpx.Response:
    SEEN.append(request)
    if request.url.path == "/moved":
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/"})
    return httpx.Response(200, json={"ok": True})


# ---- the safe client ----


@pytest.mark.parametrize(
    "url",
    [
        "http://1.1.1.1/",  # not https
        "https://127.0.0.1/",
        "https://10.0.0.8/",
        "https://169.254.169.254/latest/meta-data",  # the cloud's metadata service
        "https://[::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://localhost/",
    ],
)
async def test_the_safe_client_refuses_addresses_inside(url: str) -> None:
    async with safe_client(transport=httpx.MockTransport(_answer)) as http:
        with pytest.raises(Refused):
            await http.get(url)


async def test_the_safe_client_keeps_to_its_hosts_and_follows_no_redirect() -> None:
    async with safe_client(allowed_hosts=(HOST,), transport=httpx.MockTransport(_answer)) as http:
        with pytest.raises(Refused):
            await http.get("https://8.8.8.8/")
        moved = await http.get(f"https://{HOST}/moved")
    assert moved.status_code == 302  # handed back, not followed


def test_hosts_match_as_written() -> None:
    assert host_allowed("api.kraken.com", ["api.kraken.com"])
    assert host_allowed("eu.api.example.com", ["*.example.com"])
    assert not host_allowed("example.com", ["*.example.com"])
    assert not host_allowed("api.kraken.com.evil.test", ["api.kraken.com"])


# ---- types ----


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        (ConnectionType("x", "X", ()), "no_hosts"),
        (ConnectionType("x", "X", (HOST,), auth="signed"), "no_sign"),
        (ConnectionType("x", "X", (HOST,), auth="bearer"), "missing_secret"),
        (ConnectionType("x", "X", (HOST,), base_url="https://8.8.8.8"), "bad_url"),
        (ConnectionType("x", "X", (HOST,), base_url=f"http://{HOST}"), "bad_url"),
    ],
)
async def test_connection_types_are_checked(
    owner_engine: AsyncEngine, kind: ConnectionType, code: str
) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_types(conn, "sys", (kind,))
    assert code in [e.code for e in refused.value.errors]


# ---- connections ----


def _sign(request: httpx.Request, secrets: dict[str, str]) -> None:
    request.headers["X-Signed"] = f"{secrets['key']}:{request.url.path}"


async def _check(http: httpx.AsyncClient) -> str:
    answer = await http.get("/status")
    return "Answers." if answer.json()["ok"] else "Says no."


@pytest.fixture
def types() -> Iterator[None]:
    """Two test types, as if a module declared them (the code is all hooks and the client need)."""
    use_sealer(Sealer(os.urandom(32)))
    TYPES["sys.bearer_test"] = (
        "sys",
        ConnectionType(
            "bearer_test",
            "T",
            (HOST,),
            auth="bearer",
            secrets=("token",),
            base_url=f"https://{HOST}",
            check=_check,
        ),
    )
    TYPES["sys.signed_test"] = (
        "sys",
        ConnectionType("signed_test", "S", (HOST,), auth="signed", secrets=("key",), sign=_sign),
    )
    for name in ("sys.bearer_test", "sys.signed_test"):
        TEST_TRANSPORTS[name] = httpx.MockTransport(_answer)
    yield
    for name in ("sys.bearer_test", "sys.signed_test"):
        TYPES.pop(name, None)
        TEST_TRANSPORTS.pop(name, None)


@pytest.fixture
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def _people(engine: AsyncEngine) -> tuple[uuid.UUID, uuid.UUID]:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        anna = (await records.create("sys.person", {"name": f"Anna {tag}"})).sys_id
        bob = (await records.create("sys.person", {"name": f"Bob {tag}"})).sys_id
    return anna, bob


async def test_a_connection_signs_in_with_its_sealed_secret(
    engine: AsyncEngine, types: None
) -> None:
    anna, bob = await _people(engine)
    SEEN.clear()
    async with acting(engine, anna) as records:
        made = await records.create("sys.connection", {"type": "sys.bearer_test", "label": "Mine"})
        with pytest.raises(Problem) as elsewhere:
            await records.create(
                "sys.connection",
                {"type": "sys.bearer_test", "label": "Other", "base_url": "https://8.8.8.8"},
            )
        with pytest.raises(Problem) as unknown:
            await records.create(
                "sys.connection_secret",
                {"connection": str(made.sys_id), "name": "password", "value": "x"},
            )
        secret = await records.create(
            "sys.connection_secret",
            {"connection": str(made.sys_id), "name": "token", "value": "s3cret"},
        )
        async with connect(records, "sys.bearer_test") as http:
            await http.get("/orders")
    async with acting(engine, bob) as records:
        with pytest.raises(Problem) as not_his:
            await records.create(
                "sys.connection_secret",
                {"connection": str(made.sys_id), "name": "token", "value": "mine now"},
            )
        with pytest.raises(Problem) as not_for_bob:
            async with connect(records, "sys.bearer_test", owner=anna):
                pass
    async with acting(engine, None) as records:
        stored = await records.conn.scalar(
            text("SELECT value FROM sys.connection_secret WHERE sys_id = :i"), {"i": secret.sys_id}
        )
    assert made.values["base_url"] == f"https://{HOST}"  # the type's usual address
    assert elsewhere.value.code == unknown.value.code == not_his.value.code == "validation"
    assert "value" not in secret.values  # never read back
    assert stored.startswith("v1:") and "s3cret" not in stored  # sealed
    assert SEEN[-1].headers["Authorization"] == "Bearer s3cret"
    assert not_for_bob.value.code == "forbidden"


async def test_a_modules_job_uses_its_own_types_connections(
    engine: AsyncEngine, types: None
) -> None:
    anna, _ = await _people(engine)
    async with acting(engine, anna) as records:
        made = await records.create(
            "sys.connection",
            {"type": "sys.signed_test", "label": "Signed", "base_url": f"https://{HOST}"},
        )
        await records.create(
            "sys.connection_secret", {"connection": str(made.sys_id), "name": "key", "value": "k1"}
        )
    SEEN.clear()
    # As a job of the module runs: its own account, on the job's channel.
    with working_as(Context(channel="job:sys.cycle", person_id=uuid.uuid4())):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            async with connect(records, "sys.signed_test", owner=anna) as http:
                await http.post("/0/private/Balance")
    assert SEEN[-1].headers["X-Signed"] == "k1:/0/private/Balance"


async def test_a_check_says_whether_a_connection_works(engine: AsyncEngine, types: None) -> None:
    anna, _ = await _people(engine)
    async with acting(engine, anna) as records:
        made = await records.create(
            "sys.connection", {"type": "sys.bearer_test", "label": "Check me"}
        )
        await records.create(
            "sys.connection_secret", {"connection": str(made.sys_id), "name": "token", "value": "t"}
        )
        said = await check(records, made.sys_id)
        seen = await records.read("sys.connection", made.sys_id)
    TEST_TRANSPORTS["sys.bearer_test"] = httpx.MockTransport(
        lambda r: httpx.Response(200, content=json.dumps({"ok": False}))
    )
    async with acting(engine, anna) as records:
        said_now = await check(records, made.sys_id)
    assert (said, seen.values["health"]) == ("Answers.", "ok")
    assert said_now == "Says no."


async def test_one_connection_of_several_and_never_anothers(
    engine: AsyncEngine, types: None
) -> None:
    anna, bob = await _people(engine)
    made: dict[str, uuid.UUID] = {}
    for who, label, token in ((anna, "First", "t1"), (anna, "Second", "t2"), (bob, "Bobs", "t3")):
        async with acting(engine, who) as records:
            row = await records.create(
                "sys.connection", {"type": "sys.bearer_test", "label": label}
            )
            await records.create(
                "sys.connection_secret",
                {"connection": str(row.sys_id), "name": "token", "value": token},
            )
            made[label] = row.sys_id
    SEEN.clear()
    async with acting(engine, anna) as records:
        async with connect(records, "sys.bearer_test", connection=made["Second"]) as http:
            await http.get("/orders")
        with pytest.raises(Problem) as not_hers:
            async with connect(records, "sys.bearer_test", connection=made["Bobs"]):
                pass
    assert SEEN[-1].headers["Authorization"] == "Bearer t2"
    assert not_hers.value.code == "no_connection"


async def test_owners_are_reminded_before_a_secret_expires(
    engine: AsyncEngine, types: None
) -> None:
    anna, _ = await _people(engine)
    soon = (datetime.now(UTC).date() + timedelta(days=10)).isoformat()
    async with acting(engine, anna) as records:
        made = await records.create("sys.connection", {"type": "sys.bearer_test", "label": "Shop"})
        secret = await records.create(
            "sys.connection_secret",
            {"connection": str(made.sys_id), "name": "token", "value": "t", "expires": soon},
        )
        with pytest.raises(Problem):
            await records.update("sys.connection_secret", secret.sys_id, {"told": soon}, None)
    for _ in range(2):  # told once, not every day
        state, _said = await run_job(engine, "sys.secret_expiry")
        assert state == "succeeded"
    async with acting(engine, None) as records:
        told = (
            (
                await records.conn.execute(
                    text("SELECT title FROM sys.notification WHERE person = :p AND kind = :k"),
                    {"p": anna, "k": "sys.secret_expiring"},
                )
            )
            .scalars()
            .all()
        )
    assert told == [f"The secret token of Shop expires on {soon}"]
    async with acting(engine, anna) as records:  # a new secret: reminded again later
        current = await records.read("sys.connection_secret", secret.sys_id)
        renewed = await records.update(
            "sys.connection_secret", secret.sys_id, {"value": "t2"}, current.etag
        )
    assert renewed.values["told"] is None


# ---- connections to one's own network ----


@pytest.fixture
def local_type() -> Iterator[None]:
    use_sealer(Sealer(os.urandom(32)))
    TYPES["sys.local_test"] = ("sys", ConnectionType("local_test", "L", (), local=True))
    TEST_TRANSPORTS["sys.local_test"] = httpx.MockTransport(_answer)
    yield
    TYPES.pop("sys.local_test", None)
    TEST_TRANSPORTS.pop("sys.local_test", None)


async def test_a_local_connection_takes_ranges_as_typed_and_a_name(
    engine: AsyncEngine, local_type: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def lookup(host: str) -> list[str]:
        return {"ubi.home.example.net": ["192.168.1.7"]}.get(host, ["203.0.113.9"])

    monkeypatch.setattr(outgoing, "lookup", lookup)
    anna, _ = await _people(engine)
    async with acting(engine, anna) as records:
        made = await records.create(
            "sys.connection",
            {
                "type": "sys.local_test",
                "label": "Console",
                "base_url": "https://ubi.home.example.net",
                "local_ranges": "192.168.1.0/24\n 10.0.5.0/24, ",  # as typed in the form
            },
        )
        refused: list[str] = []
        for values in (
            {"base_url": "https://192.168.9.1"},  # an address outside its ranges
            {"base_url": "https://admin:pw@192.168.1.1"},  # no user or password in it
            {"base_url": "ftp://192.168.1.1"},
            {"local_ranges": ["0.0.0.0/0"]},
        ):
            current = await records.read("sys.connection", made.sys_id)
            with pytest.raises(Problem) as wrong:
                await records.update("sys.connection", made.sys_id, values, current.etag)
            refused.append(wrong.value.code)
        SEEN.clear()
        async with connect(records, "sys.local_test") as http:
            await http.get("/proxy/network/integration/v1/sites")
    assert made.values["local_ranges"] == ["192.168.1.0/24", "10.0.5.0/24"]
    assert refused == ["validation"] * 4
    assert SEEN[-1].url.host == "192.168.1.7"
    assert SEEN[-1].headers["host"] == "ubi.home.example.net"
