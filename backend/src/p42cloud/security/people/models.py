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
    },
    label_plural="People",
)

TABLES = [PERSON]
