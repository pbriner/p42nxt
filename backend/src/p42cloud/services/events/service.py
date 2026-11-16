"""Writing event types and subscriptions into records, and the worker delivering events.

A delivery is claimed (`working`, committed), then the subscribing module's handler runs in one
transaction with marking it `done`: both are kept, or neither. A handler that fails leaves
nothing behind and is tried again later, waiting longer each time; after its last try the
delivery fails and administrators hear of it. A worker stopping mid-delivery rolls the handler
back with it; the delivery is taken up again after a few minutes. So each subscription applies
each event once.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.declared import label_of, sync
from p42cloud.data.records import Records
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import switch_role, transaction
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.security.access.service import access_for
from p42cloud.services.events.models import EventRun, EventType, Subscription
from p42cloud.services.jobs.service import account_of, ensure_service_account
from p42cloud.services.notifications.service import send as notify

EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FULL_NAME = re.compile(r"^[a-z][a-z0-9]{1,15}\.[a-z][a-z0-9_]{0,39}$")
TABLE_KEY = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
MAX_RETRIES = 10
WAITS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
STUCK = timedelta(minutes=10)  # a delivery `working` this long: its worker stopped
KEEP = timedelta(days=90)
MAX_MESSAGE = 2000

# The installed modules' subscriptions, by record name: (module, subscription).
SUBSCRIPTIONS: dict[str, tuple[str, Subscription]] = {}


def subscription_name(module: str, event: str) -> str:
    return f"{module}:{event}"


def register(module: str, subscriptions: tuple[Subscription, ...]) -> None:
    for subscription in subscriptions:
        SUBSCRIPTIONS[subscription_name(module, subscription.event)] = (module, subscription)


def _system() -> Context:
    return Context(channel="worker", access=EVERYTHING)


# ---- declaring ----


def _type_problems(types: tuple[EventType, ...]) -> list[FieldError]:
    found: list[FieldError] = []
    for event in types:
        where = f"events.{event.name}"
        if not EVENT_NAME.match(event.name):
            found.append(FieldError(where, "bad_name", "a-z, 0-9 and _, starting with a letter."))
        if event.about is not None and not TABLE_KEY.match(event.about):
            found.append(FieldError(where, "bad_table", "A table as <module>.<table>."))
    if len({e.name for e in types}) != len(types):
        found.append(FieldError("events", "duplicate", "An event type's name is used twice."))
    return found


def _subscription_problems(subscriptions: tuple[Subscription, ...]) -> list[FieldError]:
    found: list[FieldError] = []
    for subscription in subscriptions:
        where = f"subscriptions.{subscription.event}"
        if not FULL_NAME.match(subscription.event):
            found.append(FieldError(where, "bad_event", "An event type as <module>.<name>."))
        if not 0 <= subscription.retries <= MAX_RETRIES:
            found.append(FieldError(where, "bad_retries", f"0 to {MAX_RETRIES} retries."))
    if len({s.event for s in subscriptions}) != len(subscriptions):
        found.append(FieldError("subscriptions", "duplicate", "One subscription per event type."))
    return found


async def write_events(
    conn: AsyncConnection,
    module: str,
    types: tuple[EventType, ...],
    subscriptions: tuple[Subscription, ...],
) -> None:
    """Brings the module's event types and subscriptions to its declaration (as the owner)."""
    found = _type_problems(types) + _subscription_problems(subscriptions)
    if found:
        raise Problem(422, "invalid_events", "The events can't be used.", errors=found)
    await sync(
        conn,
        "sys.event_type",
        module,
        [
            {
                "name": f"{module}.{e.name}",
                "label": label_of(e.label),
                "description": label_of(e.description),
                "about": e.about,
            }
            for e in types
        ],
        frozenset({"label", "description"}),
    )
    await sync(
        conn,
        "sys.event_subscription",
        module,
        [
            {
                "name": subscription_name(module, s.event),
                "label": label_of(s.label or f"{module} on {s.event}"),
                "event_type": s.event,
                "retries": s.retries,
            }
            for s in subscriptions
        ],
        frozenset({"label"}),
    )
    if subscriptions and module != "sys":  # the core's handlers run as the system
        await ensure_service_account(conn, module)


# ---- delivering ----


async def _claim(engine: AsyncEngine, now: datetime) -> Any | None:
    """The next delivery due, now `working`: of an enabled module, in publishing order."""
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            return (
                await conn.execute(
                    text(
                        "UPDATE sys.event_delivery d SET state = 'working',"
                        " attempts = d.attempts + 1, started = :now"
                        " FROM (SELECT o.sys_id, s.retries FROM sys.event_delivery o"
                        " JOIN sys.event e ON e.sys_id = o.event"
                        " JOIN sys.event_subscription s ON s.name = o.subscription"
                        " AND s.deleted IS NULL"
                        " JOIN sys.module m ON m.name = o.module AND m.deleted IS NULL"
                        " AND m.enabled"
                        " WHERE o.deleted IS NULL AND o.state = 'pending'"
                        " AND (o.not_before IS NULL OR o.not_before <= :now)"
                        " ORDER BY e.created, o.sys_id LIMIT 1"
                        " FOR UPDATE OF o SKIP LOCKED) due"
                        " WHERE d.sys_id = due.sys_id"
                        " RETURNING d.sys_id, d.event, d.subscription, d.module, d.attempts,"
                        " due.retries"
                    ),
                    {"now": now},
                )
            ).first()


async def _context(engine: AsyncEngine, module: str, row: Any) -> Context:
    """As the module's service account (the core's handlers: the system)."""
    channel = f"event:{row.subscription}"
    if module == "sys":
        return Context(channel=channel, request_id=row.sys_id, access=EVERYTHING)
    person = account_of(module)
    with working_as(Context(channel="worker", person_id=person)):
        async with transaction(engine, "sys") as conn:
            access = await access_for(conn, person)
    return Context(channel=channel, person_id=person, request_id=row.sys_id, access=access)


async def _handle(engine: AsyncEngine, row: Any, subscription: Subscription) -> None:
    """The handler, and `done`, in one transaction."""
    context = await _context(engine, row.module, row)
    with working_as(context):
        async with transaction(engine, "sys") as conn:
            event: Any = (
                await conn.execute(
                    text(
                        "SELECT type, data, record_table, about, created, created_by"
                        " FROM sys.event WHERE sys_id = :id"
                    ),
                    {"id": row.event},
                )
            ).one()
            records = Records(conn, await load_catalog(conn))
            await subscription.handler(
                EventRun(
                    records,
                    row.event,
                    event.type,
                    cast(dict[str, Any], event.data or {}),
                    event.record_table,
                    event.about,
                    event.created,
                    event.created_by,
                    row.attempts,
                )
            )
            await switch_role(conn, "sys")
            await conn.execute(
                text(
                    "UPDATE sys.event_delivery SET state = 'done', finished = now(),"
                    " message = NULL WHERE sys_id = :id"
                ),
                {"id": row.sys_id},
            )


def _said(error: BaseException) -> str:
    if isinstance(error, Problem):
        return f"{error.title} {error.detail or ''}".strip()[:MAX_MESSAGE]
    return f"{type(error).__name__}: {error}"[:MAX_MESSAGE]


async def _end(engine: AsyncEngine, row: Any, state: str, message: str) -> None:
    """A try that didn't work: waits for the next one, or ends the delivery."""
    last = state != "pending" or row.attempts > row.retries
    wait = WAITS[min(int(row.attempts), len(WAITS)) - 1]
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            await conn.execute(
                text(
                    "UPDATE sys.event_delivery SET state = :state, message = :message,"
                    " not_before = now() + :wait, finished = CASE WHEN :last THEN now() END"
                    " WHERE sys_id = :id"
                ),
                {
                    "state": "failed" if state == "pending" and last else state,
                    "message": message,
                    "wait": wait,
                    "last": last,
                    "id": row.sys_id,
                },
            )
            if state == "pending" and last:
                await notify(
                    conn,
                    "sys.event_failed",
                    f"An event wasn't handled: {row.subscription}",
                    roles=("sys.admin",),
                    message=message,
                    link=f"/form/sys.event_delivery/{row.sys_id}",
                    severity="error",
                )


async def deliver(engine: AsyncEngine, now: datetime | None = None) -> str | None:
    """Works on one delivery; returns its new state, or None if there was nothing to do."""
    row = await _claim(engine, now or datetime.now(UTC))
    if row is None:
        return None
    found = SUBSCRIPTIONS.get(row.subscription)
    if found is None:
        await _end(engine, row, "skipped", "The module that listened for it isn't there.")
        return "skipped"
    try:
        await _handle(engine, row, found[1])
    except Exception as error:  # whatever a handler does wrong is recorded
        await _end(engine, row, "pending", _said(error))
        return "failed" if row.attempts > row.retries else "pending"
    return "done"


async def recover(engine: AsyncEngine, now: datetime | None = None) -> int:
    """Deliveries a stopped worker left `working` (their handler's work was rolled back with
    it) are taken up again; those of removed modules or subscriptions are skipped."""
    now = now or datetime.now(UTC)
    with working_as(_system()):
        async with transaction(engine, "sys") as conn:
            stuck = await conn.execute(
                text(
                    "UPDATE sys.event_delivery SET state = 'pending', not_before = :now,"
                    " message = 'The worker stopped while handling it; trying again.'"
                    " WHERE state = 'working' AND deleted IS NULL AND started < :cutoff"
                ),
                {"now": now, "cutoff": now - STUCK},
            )
            await conn.execute(
                text(
                    "UPDATE sys.event_delivery d SET state = 'skipped', finished = :now,"
                    " message = 'The module or its subscription was removed.'"
                    " WHERE d.state = 'pending' AND d.deleted IS NULL AND NOT EXISTS"
                    " (SELECT 1 FROM sys.event_subscription s JOIN sys.module m"
                    " ON m.name = s.module AND m.deleted IS NULL"
                    " WHERE s.name = d.subscription AND s.deleted IS NULL)"
                ),
                {"now": now},
            )
            return stuck.rowcount


async def try_again(conn: AsyncConnection, delivery: uuid.UUID) -> None:
    """A failed delivery is tried again, with all its tries."""
    await switch_role(conn, "sys")
    changed = await conn.execute(
        text(
            "UPDATE sys.event_delivery SET state = 'pending', attempts = 0, not_before = now(),"
            " finished = NULL WHERE sys_id = :id AND state = 'failed' AND deleted IS NULL"
        ),
        {"id": delivery},
    )
    if changed.rowcount == 0:
        raise Problem(409, "not_failed", "Only a failed delivery can be tried again.")


async def clean_up(conn: AsyncConnection, now: datetime | None = None) -> int:
    """Puts away events 90 days old whose deliveries are all over, with their deliveries."""
    now = now or datetime.now(UTC)
    await switch_role(conn, "sys")
    done = await conn.execute(
        text(
            "UPDATE sys.event e SET deleted = now() WHERE e.deleted IS NULL"
            " AND e.created < :cutoff AND NOT EXISTS (SELECT 1 FROM sys.event_delivery d"
            " WHERE d.event = e.sys_id AND d.deleted IS NULL"
            " AND d.state IN ('pending', 'working', 'failed'))"
            " RETURNING e.sys_id"
        ),
        {"cutoff": now - KEEP},
    )
    gone = [r.sys_id for r in done]
    if gone:
        await conn.execute(
            text(
                "UPDATE sys.event_delivery SET deleted = now() WHERE deleted IS NULL"
                " AND event = ANY(:gone)"
            ),
            {"gone": gone},
        )
    return len(gone)
