"""Messages as records: their kind, subject and text, and while they're shown."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text

KINDS = {"information": "Information", "warning": "Warning", "critical": "Critical"}
DELIVERY = {"band": "Band", "notification": "Notification", "both": "Band and notification"}
IN_THE_BAND = ("band", "both")
AS_NOTIFICATION = ("notification", "both")

ANNOUNCEMENT = Table(
    "announcement",
    "Message",
    {
        # Each kind's colours are the theme's (message-<kind>, on-message-<kind>).
        "kind": Choice(
            "Kind",
            KINDS,
            required=True,
            default="information",
            tones={"warning": "tertiary", "critical": "error"},
        ),
        "subject": String("Subject", max_length=200, required=True),
        "text": Text("Message"),
        "valid_from": DateTime("Shown from", required=True),
        "valid_until": DateTime("Shown until"),
        # Empty: everyone signed in. Only the audience may read it (the read rule says so).
        "audience": Reference("Audience", "sys.group"),
        "deliver": Choice("Show as", DELIVERY, required=True, default="band"),
        # When its notifications went out: once, when it became valid.
        "notified": DateTime("Notified", read_only=True),
        # Written by the updater about this update (and ended with it).
        "about_update": Reference("About the update", "sys.update_request", read_only=True),
    },
    label_plural="Messages",
    display="subject",
)

TABLES = [ANNOUNCEMENT]
