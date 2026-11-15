"""Requesting an update (administrators, in the app) and carrying it out (the updater on the
machine, through the command line).

The app never updates itself: replacing its own containers needs control over Docker on the
machine, which the app must not have. It records what administrators ask for; the updater
service on the machine asks the running worker what's due (`p42cloud updater next`), does the
update as `deploy/update.sh` would, and reports how it went (`updater finish`). Requests are
checked: administrators only, with the second factor again, a version the signed index offers,
one update at a time.
"""

import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.settings import value
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.guards import recently_verified
from p42cloud.services.announcements import service as announcements
from p42cloud.services.notifications.service import send
from p42cloud.services.update_requests.models import OPEN

MAX_LOG = 20_000  # characters of the updater's log kept with a request
NIGHT_HOUR = 3  # "tonight": 03:00 in the installation's time zone
FINAL = ("succeeded", "put_back", "failed")
PLANNED_AFTER = timedelta(minutes=5)  # sooner than this, the update itself is the message


def tonight(zone: str, now: datetime | None = None) -> datetime:
    """The next 03:00 in the installation's time zone."""
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(zone))
    night = local.replace(hour=NIGHT_HOUR, minute=0, second=0, microsecond=0)
    if night <= local:
        night += timedelta(days=1)
    return night.astimezone(UTC)


async def _check_offered(conn: AsyncConnection, target: str, version: str) -> None:
    offered = await conn.scalar(
        text(
            "SELECT true FROM sys.module_update"
            " WHERE module = :t AND available = :v AND deleted IS NULL"
        ),
        {"t": target, "v": version},
    )
    if not offered:
        raise Problem(
            409,
            "not_offered",
            f"{target} {version} isn't among the available updates (signed releases).",
        )


async def _check_request(change: Change) -> None:
    """Administrators ask, with the second factor again; one update at a time; afterwards, a
    request can only be cancelled before it starts. The updater reports through the command
    line, not through here."""
    recently_verified(change)
    conn = change.records.conn
    if change.operation in ("delete", "restore"):
        raise Problem(409, "kept", "Update requests are kept; cancel one instead.")
    if change.operation == "create":
        unknown = set(change.values) - {"target", "version", "not_before"}
        if unknown:
            raise Problem(409, "set_by_updater", "Only what to update, to which version, and when.")
        await _check_offered(
            conn, str(change.values.get("target")), str(change.values.get("version"))
        )
        busy = await conn.scalar(
            text("SELECT true FROM sys.update_request WHERE state = ANY(:o) AND deleted IS NULL"),
            {"o": list(OPEN)},
        )
        if busy:
            raise Problem(409, "one_at_a_time", "An update is requested or running already.")
        change.values["requested_by"] = change.person
        change.values["state"] = "requested"
        change.values.setdefault("not_before", datetime.now(UTC))
        return
    state = await conn.scalar(
        text("SELECT state FROM sys.update_request WHERE sys_id = :i"), {"i": change.sys_id}
    )
    if change.values != {"state": "cancelled"} or state != "requested":
        raise Problem(409, "set_by_updater", "A requested update can only be cancelled.")


async def _announce(change: Change) -> None:
    """A planned update tells everyone beforehand; a cancelled one takes its message back."""
    conn = change.records.conn
    if change.sys_id is None:
        return
    if change.operation == "write":
        if change.values.get("state") == "cancelled":
            await announcements.end(conn, change.sys_id)
        return
    if change.operation != "create":
        return
    when = change.values.get("not_before")
    if not isinstance(when, datetime) or when <= datetime.now(UTC) + PLANNED_AFTER:
        return
    zone = str(await value(conn, "sys.time_zone") or "UTC")
    local = when.astimezone(ZoneInfo(zone))
    await announcements.post(
        conn,
        kind="information",
        subject=f"Update planned for {local:%d %B at %H:%M} ({zone})",
        message="The app will be unavailable for a few minutes then.",
        until=when,
        update=change.sys_id,
    )


HOOKS = (TableHook("sys.update_request", before=_check_request, after=_announce),)


# ---- the updater's side (command line, as the owner) ----


async def due(conn: AsyncConnection) -> dict[str, Any] | None:
    """The update to carry out now, if any."""
    found = (
        await conn.execute(
            text(
                "SELECT sys_id, target, version FROM sys.update_request"
                " WHERE state = 'requested' AND not_before <= now() AND deleted IS NULL"
                " ORDER BY not_before LIMIT 1"
            )
        )
    ).first()
    return None if found is None else dict(found._mapping)  # pyright: ignore[reportPrivateUsage]


async def start(conn: AsyncConnection, request_id: uuid.UUID) -> bool:
    """Marks it running; False if it isn't waiting any more (cancelled meanwhile)."""
    changed = await conn.execute(
        text(
            "UPDATE sys.update_request SET state = 'running', started = now(),"
            " from_version = (SELECT version FROM sys.module m"
            "   WHERE m.name = update_request.target AND m.deleted IS NULL)"
            " WHERE sys_id = :i AND state = 'requested'"
        ),
        {"i": request_id},
    )
    if changed.rowcount != 1:
        return False
    await announcements.end(conn, request_id)  # the "planned" one, if any
    await announcements.post(
        conn,
        kind="warning",
        subject="Being updated",
        message="The app may be unavailable for a few minutes.",
        until=datetime.now(UTC) + announcements.UPDATING_FOR,
        update=request_id,
    )
    return True


async def finish(  # noqa: PLR0913 - how it went, in full
    conn: AsyncConnection,
    request_id: uuid.UUID,
    *,
    state: str,
    message: str,
    backup: str | None,
    log: str,
) -> None:
    """Records how it went, and tells administrators."""
    if state not in FINAL:
        raise Problem(422, "validation", f"The result is one of {', '.join(FINAL)}.")
    found = (
        await conn.execute(
            text(
                "UPDATE sys.update_request SET state = :s, finished = now(), message = :m,"
                " backup = :b, log = :l WHERE sys_id = :i AND state = 'running'"
                " RETURNING target, version, requested_by"
            ),
            {"s": state, "m": message[:2000], "b": backup, "l": log[-MAX_LOG:], "i": request_id},
        )
    ).first()
    if found is None:
        raise Problem(409, "not_running", "That update isn't running.")
    await announcements.end(conn, request_id)
    what = "The core" if found.target == "sys" else found.target
    title = {
        "succeeded": f"Updated: {what} {found.version}",
        "put_back": f"Update failed, version before put back: {what} {found.version}",
        "failed": f"Update failed: {what} {found.version}",
    }[state]
    # The result is what counts: a notice that can't be sent doesn't undo it.
    with contextlib.suppress(Problem):
        await send(
            conn,
            "sys.update_finished",
            title,
            people=(found.requested_by,),
            roles=("sys.admin",),
            message=message[:2000] or None,
            link=f"/form/sys.update_request/{request_id}",
            severity="info" if state == "succeeded" else "error",
        )
