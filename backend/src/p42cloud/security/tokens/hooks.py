"""Revoking a token through the records service is a security event."""

from p42cloud.data.hooks import HOOKS, Change
from p42cloud.security.sign_in.events import event


async def _revoked(change: Change) -> None:
    if change.operation == "delete" and change.sys_id is not None:
        await event(change.records.conn, "api_token_revoked", token=str(change.sys_id))


def register() -> None:
    HOOKS.on("sys.api_token", after=_revoked)
