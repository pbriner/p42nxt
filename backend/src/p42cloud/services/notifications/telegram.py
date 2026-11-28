"""Telegram as a notification channel: the installation's bot, linking chats, sending.

The bot's token goes into the URL path (`/bot<token>/sendMessage`), so nothing here ever logs a
request or keeps an error's text that could hold a URL: httpx's request log is switched off and
errors are recorded by their kind only.
"""

import contextlib
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.records import Records
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import switch_role, transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.kernel.outgoing import HELD, outside_allowed
from p42cloud.services.connections.models import ConnectionType
from p42cloud.services.connections.service import connect
from p42cloud.services.notifications.models import CHANNELS
from p42cloud.services.notifications.service import KINDS

# Never log request URLs: they hold the bot's token.
logging.getLogger("httpx").setLevel(logging.WARNING)

TYPE = "sys.telegram"
TOKEN = re.compile(r"^\d{1,20}:[A-Za-z0-9_-]{20,100}$")
CODE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")  # what a Telegram start link carries
CODE_LIFE = timedelta(minutes=15)
WAITS = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
)
MAX_TEXT = 4000  # Telegram takes 4096 characters
ICONS = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❗"}  # noqa: RUF001
GONE = (400, 403)  # the chat is gone, or blocked the bot


def _sign(request: httpx.Request, secrets: dict[str, str]) -> None:
    token = secrets.get("token", "")
    if not TOKEN.match(token):  # nothing odd ever lands in the path
        token = "0:not-a-valid-token-set-it-again"  # noqa: S105 - a placeholder, not a secret
    request.url = request.url.copy_with(path=f"/bot{token}{request.url.path}")


async def _check(http: httpx.AsyncClient) -> str:
    answer = await http.get("/getMe")
    if answer.status_code != 200:  # noqa: PLR2004
        return f"Telegram says no ({answer.status_code}); check the token."
    return f"Bot @{answer.json()['result'].get('username', '?')}."


BOT = ConnectionType(
    "telegram",
    "Telegram bot",
    allowed_hosts=("api.telegram.org",),
    auth="signed",
    secrets=("token",),
    base_url="https://api.telegram.org",
    sign=_sign,
    check=_check,
    shared=True,
)


def code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class _Unreachable(Exception):
    """Telegram couldn't be asked; what went wrong, without any URL."""


def _system() -> Context:
    return Context(channel="job:sys.telegram", access=EVERYTHING)


async def _call(http: httpx.AsyncClient, method: str, path: str, **given: Any) -> httpx.Response:
    try:
        return await http.request(method, path, **given)
    except httpx.HTTPError as error:  # its text may hold the URL: only its kind is kept
        raise _Unreachable(type(error).__name__) from None


# ---- the records people change ----


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _check_link(change: Change) -> None:
    """Linking a channel: the app makes a one-time code, only its hash is kept for 15 minutes."""
    if change.operation in ("delete", "restore"):
        return
    if change.operation != "create":
        if set(change.values) - {"full_text"}:
            raise _invalid("state", "Only whether to send the whole message can change.")
        return
    channel = change.values.get("channel")
    if channel not in CHANNELS:
        raise _invalid("channel", f"One of {', '.join(CHANNELS)}.")
    code = change.values.get("code")
    if not isinstance(code, str) or not CODE.match(code):
        raise _invalid("code", "22 to 64 letters, digits, - or _.")
    conn = change.records.conn
    await switch_role(conn, "sys")
    bot = await conn.scalar(
        text("SELECT bot FROM sys.channel_state WHERE name = :c AND deleted IS NULL"),
        {"c": channel},
    )
    if not bot:
        raise Problem(409, "no_bot", "Telegram isn't set up for this installation yet.")
    linked = await conn.scalar(
        text(
            "SELECT state FROM sys.person_channel WHERE person = :p AND channel = :c"
            " AND deleted IS NULL AND state = 'linked'"
        ),
        {"p": change.person, "c": channel},
    )
    if linked:
        raise Problem(409, "linked", "Telegram is connected already; disconnect it first.")
    # An earlier code that wasn't used gives way to the new one.
    await conn.execute(
        text(
            "UPDATE sys.person_channel SET deleted = now() WHERE person = :p AND channel = :c"
            " AND deleted IS NULL AND state = 'waiting'"
        ),
        {"p": change.person, "c": channel},
    )
    change.values |= {
        "person": change.person,
        "state": "waiting",
        "code": code_hash(code),
        "expires": datetime.now(UTC) + CODE_LIFE,
        "address": None,
        "name": None,
        "bot": bot,
    }


async def _check_choice(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["person"] = change.person
    elif {"person", "kind", "channel"} & set(change.values):
        raise _invalid("kind", "A choice stays about its kind and channel; change only On.")
    kind = change.values.get("kind")
    if change.operation == "create" and kind not in KINDS:
        raise _invalid("kind", "There's no such notification kind.")


async def _check_kind(change: Change) -> None:
    channels: Any = change.values.get("channels")
    if channels is None:
        return
    if not isinstance(channels, list) or not set(cast(list[Any], channels)) <= set(CHANNELS):
        raise _invalid("channels", f"A list of channels: {', '.join(CHANNELS)}.")


TABLE_HOOKS = (
    TableHook("sys.person_channel", before=_check_link),
    TableHook("sys.notification_subscription", before=_check_choice),
    TableHook("sys.notification_kind", before=_check_kind),
)


# ---- sending ----


@dataclass(frozen=True)
class _Due:
    sys_id: uuid.UUID
    attempts: int
    title: str
    message: str | None
    link: str | None
    severity: str
    address: str
    full_text: bool
    channel_id: uuid.UUID


def _text(due: _Due, public_url: str | None) -> str:
    lines = [f"{ICONS.get(due.severity, '')} {due.title}".strip()]
    if due.full_text and due.message:
        lines += ["", due.message]
    if public_url and due.link:
        lines += ["", public_url.rstrip("/") + due.link]
    return "\n".join(lines)[:MAX_TEXT]


async def _set(conn: AsyncConnection, ident: uuid.UUID, **values: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in values)
    await conn.execute(
        # Only this function's own column names go into the text.
        text(f"UPDATE sys.notification_delivery SET {sets} WHERE sys_id = :id"),  # noqa: S608
        values | {"id": ident},
    )


def _later(attempts: int, wait: float | None = None) -> datetime | None:
    """When to try again; None when it's been tried often enough."""
    if attempts > len(WAITS):
        return None
    pause = timedelta(seconds=wait) if wait else WAITS[attempts - 1]
    return datetime.now(UTC) + pause


async def deliver(engine: AsyncEngine, public_url: str | None = None) -> str | None:
    """Sends one due message; its new state, or None if nothing was due. At least once: a stop
    between sending and saving sends it again."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            # People who disconnected get nothing more.
            await conn.execute(
                text(
                    "UPDATE sys.notification_delivery d SET state = 'failed',"
                    " message = 'Not connected any more.' WHERE d.state = 'pending'"
                    " AND d.deleted IS NULL AND NOT EXISTS (SELECT 1 FROM sys.person_channel pc"
                    " WHERE pc.person = d.person AND pc.channel = d.channel"
                    " AND pc.state = 'linked' AND pc.deleted IS NULL)"
                )
            )
            row: Any = (
                await conn.execute(
                    text(
                        "SELECT d.sys_id, d.attempts, n.title, n.message, n.link, n.severity,"
                        " pc.address, pc.full_text, pc.sys_id AS channel_id"
                        " FROM sys.notification_delivery d"
                        " JOIN sys.notification n ON n.sys_id = d.notification"
                        " JOIN sys.person_channel pc ON pc.person = d.person"
                        " AND pc.channel = d.channel AND pc.state = 'linked'"
                        " AND pc.deleted IS NULL"
                        " WHERE d.state = 'pending' AND d.deleted IS NULL"
                        " AND (d.not_before IS NULL OR d.not_before <= now())"
                        " ORDER BY d.not_before NULLS FIRST LIMIT 1"
                        " FOR UPDATE OF d SKIP LOCKED"
                    )
                )
            ).first()
            if row is None:
                return None
            due = _Due(**row._mapping)
            attempts = due.attempts + 1
            if not outside_allowed():  # a test installation: the notification in the app only
                await switch_role(conn, "sys")
                await _set(conn, due.sys_id, state="held", attempts=attempts, message=HELD)
                return "held"
            records = Records(conn, await load_catalog(conn))
            try:
                async with connect(records, TYPE) as http:
                    answer = await _call(
                        http,
                        "POST",
                        "/sendMessage",
                        json={
                            "chat_id": due.address,
                            "text": _text(due, public_url),
                            "link_preview_options": {"is_disabled": True},
                        },
                    )
            except (Problem, _Unreachable) as error:
                said = error.title if isinstance(error, Problem) else f"Unreachable: {error}"
                later = _later(attempts)
                state = "pending" if later else "failed"
                await switch_role(conn, "sys")
                await _set(
                    conn,
                    due.sys_id,
                    state=state,
                    attempts=attempts,
                    not_before=later,
                    message=said[:2000],
                )
                return state
            await switch_role(conn, "sys")
            if answer.status_code == 200:  # noqa: PLR2004
                await _set(
                    conn,
                    due.sys_id,
                    state="sent",
                    attempts=attempts,
                    sent=datetime.now(UTC),
                    message=None,
                )
                return "sent"
            if answer.status_code in GONE:
                # Blocked, or the chat is gone: unlinked, so nothing more is queued for it.
                await conn.execute(
                    text("UPDATE sys.person_channel SET deleted = now() WHERE sys_id = :id"),
                    {"id": due.channel_id},
                )
                await _set(
                    conn,
                    due.sys_id,
                    state="failed",
                    attempts=attempts,
                    message=f"Telegram refused it ({answer.status_code}); disconnected.",
                )
                return "failed"
            wait = None
            if answer.status_code == 429:  # noqa: PLR2004
                wait = float(answer.json().get("parameters", {}).get("retry_after", 30))
            later = _later(attempts, wait)
            state = "pending" if later else "failed"
            await _set(
                conn,
                due.sys_id,
                state=state,
                attempts=attempts,
                not_before=later,
                message=f"Telegram answered {answer.status_code}.",
            )
            return state


# ---- receiving: linking and unlinking chats ----


async def waiting(engine: AsyncEngine) -> bool:
    """Whether someone waits to link a chat (the bot is then asked more often)."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            return bool(
                await conn.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM sys.person_channel WHERE state = 'waiting'"
                        " AND deleted IS NULL AND expires > now())"
                    )
                )
            )


async def _reply(http: httpx.AsyncClient, chat: Any, words: str) -> None:
    # A lost reply changes nothing; the link itself is saved.
    with contextlib.suppress(_Unreachable):
        await _call(http, "POST", "/sendMessage", json={"chat_id": chat, "text": words})


async def _start(
    conn: AsyncConnection, http: httpx.AsyncClient, chat: Any, sender: Any, code: str
) -> None:
    found: Any = (
        await conn.execute(
            text(
                "UPDATE sys.person_channel SET state = 'linked', address = :chat, name = :name,"
                " code = NULL, expires = NULL WHERE state = 'waiting' AND deleted IS NULL"
                " AND channel = 'telegram' AND code = :code AND expires > now()"
                " RETURNING person"
            ),
            {
                "chat": str(chat),
                "name": str(sender.get("username") or sender.get("first_name") or "")[:120] or None,
                "code": code_hash(code),
            },
        )
    ).first()
    if found is None:
        await _reply(http, chat, "This link isn't valid any more. Make a new one in your profile.")
        return
    # One person per chat: an older link of this chat to someone else ends.
    await conn.execute(
        text(
            "UPDATE sys.person_channel SET deleted = now() WHERE channel = 'telegram'"
            " AND address = :chat AND person <> :p AND deleted IS NULL"
        ),
        {"chat": str(chat), "p": found.person},
    )
    name = await conn.scalar(
        text("SELECT name FROM sys.person WHERE sys_id = :p"), {"p": found.person}
    )
    app = await conn.scalar(
        text("SELECT value #>> '{}' FROM sys.setting WHERE name = 'sys.app_name'")
    )
    await _reply(http, chat, f"Connected to {app or 'P42cloud'} as {name}.")


async def _stop(conn: AsyncConnection, http: httpx.AsyncClient, chat: Any) -> None:
    await conn.execute(
        text(
            "UPDATE sys.person_channel SET deleted = now() WHERE channel = 'telegram'"
            " AND address = :chat AND deleted IS NULL"
        ),
        {"chat": str(chat)},
    )
    await _reply(http, chat, "Disconnected. You get no more notifications here.")


def _object(value: Any) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


async def _handle(conn: AsyncConnection, http: httpx.AsyncClient, update: Any) -> int:
    """One update from the bot: a private message, answered; 1 if it was one."""
    message = _object(_object(update).get("message"))
    chat = _object(message.get("chat"))
    if chat.get("id") is None or chat.get("type") != "private":
        return 0
    words = str(message.get("text") or "").strip()
    await switch_role(conn, "sys")
    if words.startswith("/start "):
        await _start(conn, http, chat["id"], _object(message.get("from")), words[7:].strip())
    elif words == "/stop":
        await _stop(conn, http, chat["id"])
    else:
        await _reply(http, chat["id"], "Connect this chat from your profile in the app.")
    return 1


async def poll(engine: AsyncEngine) -> int:
    """Reads what people sent the bot (start links, /stop); returns how many messages."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            records = Records(conn, await load_catalog(conn))
            await switch_role(conn, "sys")
            has_bot = await conn.scalar(
                text("SELECT count(*) FROM sys.connection WHERE type = :t AND deleted IS NULL"),
                {"t": TYPE},
            )
            if not has_bot:
                return 0
            state: Any = (
                await conn.execute(
                    text(
                        "SELECT bot, \"offset\" FROM sys.channel_state WHERE name = 'telegram'"
                        " AND deleted IS NULL"
                    )
                )
            ).first()
            seen = 0
            try:
                async with connect(records, TYPE) as http:
                    bot = state.bot if state else None
                    if not bot:
                        me = await _call(http, "GET", "/getMe")
                        if me.status_code == 200:  # noqa: PLR2004
                            bot = me.json()["result"].get("username")
                    offset = state.offset if state else None
                    answer = await _call(
                        http,
                        "GET",
                        "/getUpdates",
                        params={"timeout": 0, **({"offset": offset} if offset else {})},
                    )
                    updates: list[Any] = (
                        answer.json().get("result", []) if answer.status_code == 200 else []  # noqa: PLR2004
                    )
                    for update in updates:
                        offset = int(update["update_id"]) + 1
                        seen += await _handle(conn, http, update)
            except Problem, _Unreachable:
                return 0
            await switch_role(conn, "sys")
            values = {"bot": bot, "offset": offset}
            kept = await conn.execute(
                text(
                    'UPDATE sys.channel_state SET bot = :bot, "offset" = :offset,'
                    " polled = now() WHERE name = 'telegram' AND deleted IS NULL"
                ),
                values,
            )
            if not kept.rowcount:
                await conn.execute(
                    text(
                        'INSERT INTO sys.channel_state (name, bot, "offset", polled)'
                        " VALUES ('telegram', :bot, :offset, now())"
                    ),
                    values,
                )
            return seen
