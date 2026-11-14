"""Telegram: the installation's bot, linking a chat with a one-time code, sending, unlinking."""

import itertools
import json
import os
import secrets
import uuid
from collections.abc import AsyncGenerator, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer, use_sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.services.connections.service import TEST_TRANSPORTS
from p42cloud.services.notifications import telegram
from p42cloud.services.notifications.models import NotificationKind
from p42cloud.services.notifications.service import KINDS, send, write_kinds
from tests.security.helpers import acting, change

TOKEN = "123456:" + "A" * 35
URGENT = "sys.urgent_test"
# Telegram numbers updates upwards, across tests too (the bot keeps where it read up to).
UPDATE_IDS = itertools.count(1_000)
QUIET = "sys.quiet_test"


class FakeTelegram:
    """Answers like the Bot API; keeps what was sent and what people sent the bot."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.paths: list[str] = []
        self.answer_send = 200

    def message(self, chat: int, words: str, username: str = "pat") -> None:
        self.updates.append(
            {
                "update_id": next(UPDATE_IDS),
                "message": {
                    "chat": {"id": chat, "type": "private"},
                    "from": {"username": username},
                    "text": words,
                },
            }
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "p42_test_bot"}})
        if method == "getUpdates":
            offset = int(request.url.params.get("offset", 0))
            return httpx.Response(
                200,
                json={"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]},
            )
        body = json.loads(request.content)
        self.sent.append(body)
        if self.answer_send == 429:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}})
        return httpx.Response(self.answer_send, json={"ok": self.answer_send == 200})


@pytest.fixture
def fake() -> Iterator[FakeTelegram]:
    use_sealer(Sealer(os.urandom(32)))
    made = FakeTelegram()
    TEST_TRANSPORTS[telegram.TYPE] = httpx.MockTransport(made)
    KINDS[URGENT] = "sys"
    KINDS[QUIET] = "sys"
    yield made
    TEST_TRANSPORTS.pop(telegram.TYPE, None)


@pytest.fixture
async def engine(upgraded: Settings, fake: FakeTelegram) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    async with acting(made, None) as records:
        for name, channels in ((URGENT, ["telegram"]), (QUIET, [])):
            await records.conn.execute(
                text(
                    "INSERT INTO sys.notification_kind (name, module, label, channels, custom)"
                    " SELECT CAST(:n AS varchar), 'sys', '{\"en\": \"Test\"}',"
                    " CAST(:c AS jsonb), false"
                    " WHERE NOT EXISTS (SELECT 1 FROM sys.notification_kind WHERE name = :n)"
                ),
                {"n": name, "c": json.dumps(channels)},
            )
    await _bot(made)
    yield made
    await made.dispose()


async def _person(engine: AsyncEngine, name: str, admin: bool = False) -> uuid.UUID:
    async with acting(engine, None) as records:
        person = (
            await records.create("sys.person", {"name": f"{name} {uuid.uuid4().hex[:6]}"})
        ).sys_id
        if admin:
            await records.create(
                "sys.membership", {"person": str(person), "group": str(ADMINISTRATORS)}
            )
    return person


async def _bot(engine: AsyncEngine) -> None:
    """The installation's bot, set up once by an administrator (its token sealed)."""
    async with acting(engine, None) as records:
        there = await records.conn.scalar(
            text("SELECT count(*) FROM sys.connection WHERE type = :t AND deleted IS NULL"),
            {"t": telegram.TYPE},
        )
    if there:
        async with acting(engine, None) as records:  # sealed with this test's master key
            await records.conn.execute(
                text("UPDATE sys.connection SET deleted = now() WHERE type = :t"),
                {"t": telegram.TYPE},
            )
    admin = await _person(engine, "Admin", admin=True)
    async with acting(engine, admin) as records:
        made = await records.create("sys.connection", {"type": telegram.TYPE, "label": "Bot"})
        await records.create(
            "sys.connection_secret",
            {"connection": str(made.sys_id), "name": "token", "value": TOKEN},
        )
    await telegram.poll(engine)  # learns the bot's name


async def _link(engine: AsyncEngine, fake: FakeTelegram, person: uuid.UUID, chat: int) -> str:
    code = secrets.token_urlsafe(24)
    async with acting(engine, person) as records:
        made = await records.create("sys.person_channel", {"channel": "telegram", "code": code})
    assert made.values["bot"] == "p42_test_bot" and made.values["state"] == "waiting"
    assert "code" not in made.values  # never read back
    fake.message(chat, f"/start {code}")
    await telegram.poll(engine)
    return code


async def _channel(engine: AsyncEngine, person: uuid.UUID) -> dict[str, Any] | None:
    async with acting(engine, person) as records:
        page = await records.list("sys.person_channel")
    return (
        dict(page.records[0].values) | {"sys_id": page.records[0].sys_id} if page.records else None
    )


async def _drain(engine: AsyncEngine) -> list[str]:
    states: list[str] = []
    while (state := await telegram.deliver(engine, "https://p42.example.com")) is not None:
        states.append(state)
    return states


async def test_only_an_administrator_sets_up_the_one_bot(engine: AsyncEngine) -> None:
    pat = await _person(engine, "Pat")
    admin = await _person(engine, "Admin", admin=True)
    async with acting(engine, pat) as records:
        with pytest.raises(Problem) as not_pat:
            await records.create("sys.connection", {"type": telegram.TYPE, "label": "Mine"})
    async with acting(engine, admin) as records:
        with pytest.raises(Problem) as second:
            await records.create("sys.connection", {"type": telegram.TYPE, "label": "Another"})
    assert not_pat.value.status == 403
    assert second.value.code == "validation"


async def test_a_chat_is_linked_with_a_one_time_code_and_gets_notifications(
    engine: AsyncEngine, fake: FakeTelegram
) -> None:
    pat = await _person(engine, "Pat")
    await _drain(engine)
    code = await _link(engine, fake, pat, chat=555)
    linked = await _channel(engine, pat)
    assert linked is not None and linked["state"] == "linked" and linked["name"] == "pat"
    assert "address" not in linked  # the chat is never shown
    assert fake.sent[-1]["chat_id"] == 555 and "Connected to" in fake.sent[-1]["text"]

    fake.message(556, f"/start {code}")  # used once only
    await telegram.poll(engine)
    assert "isn't valid any more" in fake.sent[-1]["text"]

    async with acting(engine, None) as records:
        await send(
            records.conn,
            URGENT,
            "Your order went through",
            people=[pat],
            message="50 CHF of bitcoin",
            link="/form/sys.person/new",
            severity="success",
        )
        await send(records.conn, QUIET, "Nothing urgent", people=[pat])
    assert await _drain(engine) == ["sent"]  # the quiet kind isn't on Telegram by default
    sent = fake.sent[-1]
    assert sent["chat_id"] == "555"
    assert sent["text"].startswith("✅ Your order went through")
    assert "50 CHF" not in sent["text"]  # only the title, unless they want the whole message
    assert "https://p42.example.com/form/sys.person/new" in sent["text"]
    assert fake.paths[-1] == f"/bot{TOKEN}/sendMessage"

    async with acting(engine, pat) as records:
        await change(records, "sys.person_channel", linked["sys_id"], {"full_text": True})
        await records.create(
            "sys.notification_subscription", {"kind": URGENT, "channel": "telegram", "on": False}
        )
        await records.create(
            "sys.notification_subscription", {"kind": QUIET, "channel": "telegram", "on": True}
        )
        with pytest.raises(Problem):
            await change(records, "sys.person_channel", linked["sys_id"], {"state": "waiting"})
    async with acting(engine, None) as records:
        await send(records.conn, URGENT, "Muted now", people=[pat])
        await send(records.conn, QUIET, "Wanted now", people=[pat], message="All of it")
    assert await _drain(engine) == ["sent"]
    assert fake.sent[-1]["text"] == "ℹ️ Wanted now\n\nAll of it"  # noqa: RUF001


async def test_stop_and_a_blocked_bot_unlink_the_chat(
    engine: AsyncEngine, fake: FakeTelegram
) -> None:
    pat = await _person(engine, "Pat")
    ann = await _person(engine, "Ann")
    await _drain(engine)
    await _link(engine, fake, pat, chat=600)
    await _link(engine, fake, ann, chat=601)
    fake.message(600, "/stop")
    await telegram.poll(engine)
    assert await _channel(engine, pat) is None
    assert "Disconnected" in fake.sent[-1]["text"]

    fake.answer_send = 403
    async with acting(engine, None) as records:
        await send(records.conn, URGENT, "Hello", people=[ann, pat])
    assert await _drain(engine) == ["failed"]  # Ann's; Pat gets none any more
    assert await _channel(engine, ann) is None


async def test_telegram_being_busy_is_waited_for(engine: AsyncEngine, fake: FakeTelegram) -> None:
    pat = await _person(engine, "Pat")
    await _drain(engine)
    await _link(engine, fake, pat, chat=700)
    fake.answer_send = 429
    async with acting(engine, None) as records:
        await send(records.conn, URGENT, "Later", people=[pat])
    assert await _drain(engine) == ["pending"]  # and then not due for a while
    async with acting(engine, None) as records:
        row = (
            await records.conn.execute(
                text(
                    "SELECT d.attempts, d.not_before > now() AS later FROM"
                    " sys.notification_delivery d WHERE d.person = :p"
                ),
                {"p": pat},
            )
        ).one()
    assert (row.attempts, row.later) == (1, True)


async def test_linking_needs_a_proper_code(engine: AsyncEngine) -> None:
    pat = await _person(engine, "Pat")
    async with acting(engine, pat) as records:
        with pytest.raises(Problem) as short:
            await records.create("sys.person_channel", {"channel": "telegram", "code": "abc"})
    assert short.value.code == "validation"


async def test_upgrades_keep_the_channels_an_administrator_chose(
    engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    async def upgrade(*kinds: NotificationKind) -> dict[str, Any]:
        with working_as(Context(channel="upgrade")):
            async with owner_transaction(owner_engine) as conn:
                await write_kinds(conn, "zz", kinds)
                rows = await conn.execute(
                    text("SELECT name, channels FROM sys.notification_kind WHERE module = 'zz'")
                )
                return {r.name: r.channels for r in rows}

    first = await upgrade(
        NotificationKind("a", "A"), NotificationKind("b", "B", channels=("telegram",))
    )
    async with acting(engine, None) as records:
        await records.conn.execute(
            text("UPDATE sys.notification_kind SET channels = '[\"telegram\"]' WHERE name = 'zz.a'")
        )
    later = await upgrade(NotificationKind("a", "A"), NotificationKind("b", "B"))
    assert first == {"zz.a": [], "zz.b": ["telegram"]}
    assert later == {"zz.a": ["telegram"], "zz.b": []}  # kept; followed the module
