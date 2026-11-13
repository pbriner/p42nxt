"""The first administrator of a new installation, made at the command line.

Only while the Administrators group has no active member: after that, people are added in the
app by those already there. The person gets a password and an authenticator app at once, so the
first sign-in already needs the second factor. Everything happens in one transaction: if the
app's code never comes right, nothing is made.
"""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.records import Records
from p42cloud.kernel.context import EVERYTHING, Context, working_as
from p42cloud.kernel.db import switch_role, transaction
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import Sealer
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.security.sign_in import credentials, passwords

ATTEMPTS = 3

_ADMINS = text(
    "SELECT count(*) FROM sys.membership m JOIN sys.person p ON p.sys_id = m.person"
    ' WHERE m."group" = :group AND m.deleted IS NULL AND p.deleted IS NULL AND p.active'
)


async def create_first_admin(  # noqa: PLR0913 - what the operator types, and how to ask for the code
    engine: AsyncEngine,
    sealer: Sealer,
    *,
    name: str,
    email: str,
    password: str,
    ask_code: Callable[[credentials.NewKey], Awaitable[str]],
    group: UUID = ADMINISTRATORS,
) -> list[str]:
    """Makes the person, puts them into Administrators, and returns their recovery codes."""
    passwords.check(password, email)
    with working_as(Context(channel="cli:create-admin", access=EVERYTHING)):
        async with transaction(engine, "sys") as conn:
            if await conn.scalar(_ADMINS, {"group": group}):
                raise Problem(
                    409,
                    "admin_exists",
                    "This installation has an administrator already: add people in the app.",
                )
            records = Records(conn, await load_catalog(conn))
            person: UUID = (
                await records.create("sys.person", {"name": name, "email": email})
            ).sys_id
            await records.create("sys.membership", {"group": group, "person": person})
            await credentials.set_password(conn, person, password)
            key = await credentials.start_totp(conn, sealer, person, "Authenticator app")
            for _ in range(ATTEMPTS):
                code = await ask_code(key)
                try:
                    async with conn.begin_nested():
                        codes = await credentials.confirm_totp(
                            conn, sealer, person, key.credential, code
                        )
                except Problem:
                    await switch_role(conn, "sys")
                    continue
                return codes
            raise Problem(422, "wrong_code", "The code was wrong too often; nothing was made.")
