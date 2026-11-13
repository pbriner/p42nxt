"""API tokens. People see and revoke their own; administrators see and revoke anyone's."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String

API_TOKEN = Table(
    "api_token",
    "API token",
    {
        "person": Reference("Acts as", "sys.person", required=True),
        "name": String("Name", max_length=80, required=True),
        # The hash of the token; the token itself is shown once and never stored.
        "token": String("Token hash", max_length=64, required=True, unique=True),
        # The token's first characters, to tell tokens apart.
        "prefix": String("Starts with", max_length=12, required=True),
        # The roles it may use (names); empty: all of the account's roles.
        "roles": Json("Only these roles"),
        "expires": DateTime("Expires", required=True),
        "last_used": DateTime("Last used"),
    },
    label_plural="API tokens",
)

TABLES = [API_TOKEN]
