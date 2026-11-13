"""Ending a session through the records service (someone ends their own, an admin ends one)."""

from p42cloud.data.hooks import Change, TableHook
from p42cloud.security.sign_in.events import event


async def _ended(change: Change) -> None:
    if change.operation == "delete" and change.sys_id is not None:
        await event(change.records.conn, "session_ended", session=str(change.sys_id))


TABLE_HOOKS = (TableHook("sys.session", after=_ended),)
