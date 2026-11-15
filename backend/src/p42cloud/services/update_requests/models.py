"""Update requests as records: what to update, to which version, from when, and how it went."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text

STATES = {
    "requested": "Requested",
    "running": "Running",
    "succeeded": "Succeeded",
    "put_back": "Failed, version before put back",
    "failed": "Failed",
    "cancelled": "Cancelled",
}
OPEN = ("requested", "running")

UPDATE_REQUEST = Table(
    "update_request",
    "Update",
    {
        # The core (sys) or a module, by its name, and linked to its record.
        "target": String("Module name", max_length=16, required=True),
        "target_ref": Reference("Module", "sys.module", key="target"),
        "version": String("To version", max_length=40, required=True),
        "not_before": DateTime("Not before", required=True),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="requested",
            tones={
                "running": "tertiary",
                "succeeded": "primary",
                "put_back": "error",
                "failed": "error",
                "cancelled": "muted",
            },
        ),
        "requested_by": Reference("Requested by", "sys.person", required=True),
        "from_version": String("From version", max_length=40),
        "started": DateTime("Started"),
        "finished": DateTime("Finished"),
        "message": Text("Result"),
        "backup": String("Backup made before", max_length=500),
        "log": Text("What the updater did"),
    },
    label_plural="Updates",
    display="target",
)

TABLES = [UPDATE_REQUEST]
