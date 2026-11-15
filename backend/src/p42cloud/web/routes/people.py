"""What administrators do for people that isn't a plain record change: invitations and resets."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from p42cloud.data import installation
from p42cloud.security.sign_in.invitations import invite
from p42cloud.web.caller import Caller, language, who
from p42cloud.web.permissions import SIGNED_IN, requires
from p42cloud.web.services import Services

router = APIRouter(prefix="/api/v1/people", tags=["people"])
Who = Annotated[Caller, Depends(who)]


class Invitation(BaseModel):
    # A reset first ends the person's sessions and retires all their credentials.
    reset: bool = False


@router.post("/{person}/invitation")
@requires(SIGNED_IN)
async def make_invitation(
    person: UUID, body: Invitation, request: Request, caller: Who
) -> dict[str, Any]:
    """A one-time link (7 days) for the person to set a password and a second factor; for the
    administrator to pass on. The token is after `#`, so it never reaches a server's logs."""
    services: Services = request.app.state.services
    async with services.records(caller.context(language(request))) as records:
        made = await invite(records.conn, person, reset=body.reset)
    base = (installation.public_url(services.settings) or "").rstrip("/")
    return {"link": f"{base}/sign-in/invitation#{made.token}", "expires": made.expires.isoformat()}
