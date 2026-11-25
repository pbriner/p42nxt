"""Making API tokens. Reading and revoking them go through the records API (`sys.api_token`).

    POST /api/v1/tokens   a new token, shown once

People make tokens for themselves, administrators for service accounts; either needs the second
factor given in the last 15 minutes (`verify_again` otherwise).
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from p42cloud.security.tokens.service import make_token
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1", tags=["platform"])
Who = Annotated[Caller, Depends(who)]


Role = Annotated[str, Field(max_length=120)]
Network = Annotated[str, Field(max_length=64)]


class TokenBody(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    # Empty: the caller; a service account's ID: for it (administrators only).
    person: UUID | None = None
    roles: Annotated[list[Annotated[str, Field(max_length=120)]], Field(max_length=50)] | None = (
        None
    )
    expires: datetime | None = None
    addresses: (
        Annotated[list[Annotated[str, Field(max_length=64)]], Field(max_length=50)] | None
    ) = None


@router.post("/tokens", status_code=201)
@requires(SIGNED_IN)
async def new_token(body: TokenBody, request: Request, caller: Who) -> dict[str, Any]:
    """A new API token; the token itself is in this answer only."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        made = await make_token(
            records.conn,
            body.person or caller.person,
            body.name,
            roles=body.roles,
            expires=body.expires,
            addresses=body.addresses,
        )
    return {
        "sys_id": str(made.sys_id),
        "token": made.token,
        "prefix": made.token[:12],
        "expires": made.expires.isoformat(),
    }
