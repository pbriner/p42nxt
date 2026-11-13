"""People: everyone who signs in or acts in the system, of one kind each."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.email import Email
from p42cloud.data.types.string import String

PERSON = Table(
    "person",
    "Person",
    {
        "name": String("Name", max_length=120, required=True),
        "email": Email("Email", unique=True, personal=True),
        "kind": Choice(
            "Kind",
            {"employee": "Employee", "external": "External", "service": "Service account"},
            required=True,
            default="employee",
        ),
        # An inactive person has no access at all, whatever their groups say.
        "active": Boolean("Active", required=True, default=True),
        # Keeps signing in with a password and a second factor even where a sign-in provider
        # claims their email's domain, so a provider's outage can't lock everyone out.
        "emergency_access": Boolean("Emergency access", required=True, default=False),
    },
    label_plural="People",
)

# What a person may change about themselves (their profile). Group rules can't use these, so
# nobody can put themselves into a group.
SELF_SERVICE = frozenset({"name", "email"})

TABLES = [PERSON]
