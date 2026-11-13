"""Sign-in providers, and the sign-ins on their way through one."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.secret import Secret
from p42cloud.data.types.string import String
from p42cloud.data.types.url import Url

IDENTITY_PROVIDER = Table(
    "identity_provider",
    "Sign-in provider",
    {
        "name": String("Name", max_length=80, required=True, unique=True),
        "issuer": Url("Issuer", required=True),
        "client_id": String("Client ID", max_length=255, required=True),
        # Sealed with the master key; set on its own, never as a plain field.
        "client_secret": Secret("Client secret"),
        "scopes": String("Scopes", max_length=255, default="openid email profile"),
        # Which kind of person it signs in; people of another kind can't use it.
        "kind": Choice(
            "Signs in",
            {"employee": "Employees", "external": "External people"},
            required=True,
            default="employee",
        ),
        # Email domains it claims: those addresses always sign in through it.
        "domains": Json("Email domains"),
        "create_people": Boolean(
            "Creates people at their first sign-in", required=True, default=False
        ),
        # The provider vouches for every email it sends (a company's own tenant), so an existing
        # person may be linked by email even without an email_verified claim.
        "trust_email": Boolean("Vouches for emails", required=True, default=False),
        # The claim listing the person's groups there (Entra: "groups"), for groups that follow it.
        "groups_claim": String("Groups claim", max_length=80),
        # Person fields filled from claims at every sign-in: {"field": "claim"}.
        "claims": Json("Fields from claims"),
        "active": Boolean("Active", required=True, default=True),
    },
    label_plural="Sign-in providers",
)

SIGN_IN_FLOW = Table(
    "sign_in_flow",
    "Sign-in through a provider",
    {
        "provider": Reference("Provider", "sys.identity_provider", required=True),
        "state": String("State hash", max_length=64, required=True, unique=True),
        "nonce": String("Nonce", max_length=64, required=True),
        "verifier": Secret("PKCE verifier", required=True),
        "expires": DateTime("Expires", required=True),
    },
    label_plural="Sign-ins through providers",
)

TABLES = [IDENTITY_PROVIDER, SIGN_IN_FLOW]
