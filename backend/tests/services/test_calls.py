"""Outside calls: recorded with a change, delivered by the worker, made at most once."""

import os
import uuid
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.records import Records
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer, use_sealer
from p42cloud.services.calls.models import CallRun, OutsideCall, Refused
from p42cloud.services.calls.service import CALLS, deliver, record, recover, write_calls
from p42cloud.services.connections.models import ConnectionType
from p42cloud.services.connections.service import TEST_TRANSPORTS, TYPES
from tests.security.helpers import acting

HOST = "1.1.1.1"
TYPE = "sys.call_test"
CALL = "sys.order_test"
LATER = timedelta(days=1)  # far enough for any wait to be over

# What the outside system did, by key; and how it behaves next.
PLACED: dict[str, dict[str, Any]] = {}
BEHAVIOUR: dict[str, str] = {}
DONE: list[tuple[str, dict[str, Any]]] = []


def _exchange(request: httpx.Request) -> httpx.Response:
    key = request.url.params.get("key", "")
    if request.method == "GET":  # asked whether it happened
        if key in PLACED:
            return httpx.Response(200, json=PLACED[key])
        return httpx.Response(404)
    how = BEHAVIOUR.pop("next", "ok")
    if how == "refuse":
        return httpx.Response(400, json={"error": "no funds"})
    if how == "unreachable":
        raise httpx.ConnectError("no route")
    PLACED[key] = {"order": f"O-{len(PLACED) + 1}"}
    if how == "lose":  # placed, but the answer never arrives
        raise httpx.ReadTimeout("timed out")
    return httpx.Response(200, json=PLACED[key])


async def _send(http: httpx.AsyncClient, run: CallRun) -> dict[str, Any]:
    answer = await http.post("/orders", params={"key": run.key}, json=run.payload)
    if answer.status_code == 400:
        raise Refused(answer.json()["error"])
    answer.raise_for_status()
    return answer.json()


async def _reconcile(http: httpx.AsyncClient, run: CallRun) -> dict[str, Any] | None:
    answer = await http.get("/orders", params={"key": run.key})
    return answer.json() if answer.status_code == 200 else None


async def _done(records: Records, run: CallRun, answer: dict[str, Any]) -> None:
    DONE.append((run.key, answer))


@pytest.fixture
def declared() -> Iterator[None]:
    """A connection type and a call, as if the core declared them."""
    use_sealer(Sealer(os.urandom(32)))
    TYPES[TYPE] = (
        "sys",
        ConnectionType(
            "call_test",
            "T",
            (HOST,),
            auth="bearer",
            secrets=("token",),
            base_url=f"https://{HOST}",
        ),
    )
    TEST_TRANSPORTS[TYPE] = httpx.MockTransport(_exchange)
    CALLS[CALL] = ("sys", OutsideCall("order_test", "Order", "call_test", _send, _reconcile, _done))
    PLACED.clear()
    BEHAVIOUR.clear()
    DONE.clear()
    yield
    TYPES.pop(TYPE, None)
    TEST_TRANSPORTS.pop(TYPE, None)
    CALLS.pop(CALL, None)


@pytest.fixture
async def engine(
    upgraded: Settings, owner_engine: AsyncEngine, declared: None
) -> AsyncGenerator[AsyncEngine]:
    with working_as(Context(channel="upgrade")):
        async with owner_transaction(owner_engine) as conn:
            await write_calls(conn, "sys", (CALLS[CALL][1],), {"call_test"})
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def _settle(engine: AsyncEngine, now: datetime | None = None) -> None:
    """Delivers whatever is due, so each test starts from a quiet queue."""
    while await deliver(engine, now) is not None:
        pass


async def _person_with_connection(engine: AsyncEngine) -> uuid.UUID:
    async with acting(engine, None) as records:
        anna = (await records.create("sys.person", {"name": f"Anna {uuid.uuid4().hex[:6]}"})).sys_id
    async with acting(engine, anna) as records:
        made = await records.create("sys.connection", {"type": TYPE, "label": "Broker"})
        await records.create(
            "sys.connection_secret",
            {"connection": str(made.sys_id), "name": "token", "value": "t"},
        )
    return anna


async def _state(engine: AsyncEngine, ident: uuid.UUID) -> dict[str, Any]:
    async with acting(engine, None) as records:
        return dict((await records.read("sys.outside_call", ident)).values)


async def test_a_call_is_made_after_the_change_is_kept(engine: AsyncEngine, declared: None) -> None:
    await _settle(engine, datetime.now(UTC) + LATER)
    anna = await _person_with_connection(engine)
    with pytest.raises(RuntimeError):
        async with acting(engine, anna) as records:
            await record(records, CALL, {"pair": "XBTCHF"})
            raise RuntimeError("the change is undone")
    assert await deliver(engine) is None  # undone with it
    async with acting(engine, anna) as records:
        ident = await record(records, CALL, {"pair": "XBTCHF"}, about=("sys.person", anna))
    assert await deliver(engine) == "done"
    seen = await _state(engine, ident)
    assert seen["state"] == "done" and seen["attempts"] == 1
    assert seen["answer"] == {"order": "O-1"}
    assert [(seen["key"], {"order": "O-1"})] == DONE


async def test_a_lost_answer_is_asked_about_before_sending_again(
    engine: AsyncEngine, declared: None
) -> None:
    await _settle(engine, datetime.now(UTC) + LATER)
    anna = await _person_with_connection(engine)
    async with acting(engine, anna) as records:
        ident = await record(records, CALL, {"pair": "XBTCHF"})
    BEHAVIOUR["next"] = "lose"
    assert await deliver(engine) == "unknown"
    assert await deliver(engine) is None  # it waits first
    assert await deliver(engine, datetime.now(UTC) + LATER) == "done"
    seen = await _state(engine, ident)
    assert len(PLACED) == 1  # placed once, found by its key
    assert seen["answer"] == {"order": "O-1"} and seen["attempts"] == 2


async def test_a_call_that_didnt_happen_is_sent_again(engine: AsyncEngine, declared: None) -> None:
    await _settle(engine, datetime.now(UTC) + LATER)
    anna = await _person_with_connection(engine)
    async with acting(engine, anna) as records:
        ident = await record(records, CALL, {})
    BEHAVIOUR["next"] = "unreachable"
    assert await deliver(engine) == "pending"
    assert await deliver(engine, datetime.now(UTC) + LATER) == "done"
    assert (await _state(engine, ident))["attempts"] == 2


async def test_a_definite_no_fails_the_call(engine: AsyncEngine, declared: None) -> None:
    await _settle(engine, datetime.now(UTC) + LATER)
    anna = await _person_with_connection(engine)
    async with acting(engine, anna) as records:
        ident = await record(records, CALL, {})
    BEHAVIOUR["next"] = "refuse"
    assert await deliver(engine) == "failed"
    assert await deliver(engine, datetime.now(UTC) + LATER) is None  # never again
    seen = await _state(engine, ident)
    assert seen["message"] == "no funds" and not PLACED and not DONE


async def test_calls_are_made_for_oneself(engine: AsyncEngine, declared: None) -> None:
    anna = await _person_with_connection(engine)
    bob = await _person_with_connection(engine)
    async with acting(engine, bob) as records:
        with pytest.raises(Problem) as for_anna:
            await record(records, CALL, {}, owner=anna)
        with pytest.raises(Problem) as unknown:
            await record(records, "sys.no_such_call", {})
        mine = await record(records, CALL, {})
    async with acting(engine, anna) as records:
        seen = await records.list("sys.outside_call")
    assert for_anna.value.code == "forbidden" and unknown.value.status == 404
    assert mine not in [r.sys_id for r in seen.records]  # Anna sees only her own


async def test_a_call_left_sending_is_asked_about(engine: AsyncEngine, declared: None) -> None:
    await _settle(engine, datetime.now(UTC) + LATER)
    anna = await _person_with_connection(engine)
    async with acting(engine, anna) as records:
        ident = await record(records, CALL, {})
    async with acting(engine, None) as records:
        await records.conn.execute(
            text(
                "UPDATE sys.outside_call SET state = 'sending', attempts = 1,"
                " sent = now() - interval '1 hour' WHERE sys_id = :i"
            ),
            {"i": ident},
        )
    assert await recover(engine) == 1
    assert (await _state(engine, ident))["state"] == "unknown"
    assert await deliver(engine) == "done"  # nothing found by its key, so sent
    assert len(PLACED) == 1
