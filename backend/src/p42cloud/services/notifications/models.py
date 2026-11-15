"""Notification kinds and each person's notifications as records, and how modules declare kinds."""

from dataclasses import dataclass

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.secret import Secret
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText

# Where notifications go besides the app (which always gets them).
CHANNELS = {"telegram": "Telegram"}

SEVERITIES = {"info": "Information", "success": "Success", "warning": "Warning", "error": "Problem"}

NOTIFICATION_KIND = Table(
    "notification_kind",
    "Notification kind",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "label": TranslatedText("Label", required=True),
        "description": TranslatedText("Description"),
        # The channels people get it on unless they chose; administrators may change them, and
        # an upgrade replaces only what nobody changed (`declared_channels`, the module's own).
        "channels": Json("Channels by default"),
        "declared_channels": Json("Channels the module suggests"),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Notification kinds",
    display="label",
)

NOTIFICATION = Table(
    "notification",
    "Notification",
    {
        # Personal: only this person sees it, administrators included.
        "person": Reference("For", "sys.person", required=True),
        "kind": String("Kind name", max_length=120, required=True),
        "kind_ref": Reference("Kind", "sys.notification_kind", key="kind"),
        "title": String("Title", max_length=200, required=True),
        "message": Text("Message"),
        # A path inside the app, never another site.
        "link": String("Link", max_length=500),
        "severity": Choice(
            "Severity",
            SEVERITIES,
            required=True,
            default="info",
            tones={"success": "primary", "warning": "tertiary", "error": "error"},
        ),
        "read": Boolean("Read", required=True, default=False),
    },
    label_plural="Notifications",
    owner="person",
    display="title",
)

PERSON_CHANNEL = Table(
    "person_channel",
    "Linked channel",
    {
        "person": Reference("Person", "sys.person", required=True),
        "channel": Choice("Channel", CHANNELS, required=True),
        "state": Choice(
            "State",
            {"waiting": "Waiting for the code", "linked": "Connected"},
            required=True,
            default="waiting",
            tones={"linked": "primary", "waiting": "tertiary"},
        ),
        # The one-time code's hash while waiting: the app makes the code and shows it once.
        "code": Secret("Code"),
        "expires": DateTime("Code expires"),
        # The chat, once linked: never shown, masked in the audit log.
        "address": Secret("Chat"),
        "name": String("Connected as", max_length=120),
        "bot": String("Bot", max_length=120),
        # Only the title by default: details such as amounts stay in the app.
        "full_text": Boolean("Send the whole message", required=True, default=False),
    },
    label_plural="Linked channels",
    owner="person",
    display="channel",
    unique_together=(("person", "channel"),),
)

SUBSCRIPTION = Table(
    "notification_subscription",
    "Notification choice",
    {
        "person": Reference("Person", "sys.person", required=True),
        "kind": String("Kind name", max_length=120, required=True),
        "kind_ref": Reference("Kind", "sys.notification_kind", key="kind"),
        "channel": Choice("Channel", CHANNELS, required=True),
        "on": Boolean("On", required=True, default=True),
    },
    label_plural="Notification choices",
    owner="person",
    display="kind",
    unique_together=(("person", "kind", "channel"),),
)

DELIVERY = Table(
    "notification_delivery",
    "Notification delivery",
    {
        "notification": Reference("Notification", "sys.notification", required=True),
        "person": Reference("For", "sys.person", required=True),
        "channel": Choice("Channel", CHANNELS, required=True),
        "state": Choice(
            "State",
            {"pending": "Waiting", "sent": "Sent", "failed": "Failed"},
            required=True,
            default="pending",
            tones={"sent": "primary", "failed": "error"},
        ),
        "attempts": Integer("Attempts", required=True, default=0),
        "not_before": DateTime("Not before"),
        "sent": DateTime("Sent"),
        "message": Text("Last said"),
    },
    label_plural="Notification deliveries",
    display="channel",
)

CHANNEL_STATE = Table(
    "channel_state",
    "Channel state",
    {
        "name": String("Channel", max_length=40, required=True, unique=True),
        "bot": String("Bot", max_length=120),
        "offset": Integer("Read up to"),
        "polled": DateTime("Last asked"),
    },
    label_plural="Channel states",
    display="name",
)

TABLES = [NOTIFICATION_KIND, NOTIFICATION, PERSON_CHANNEL, SUBSCRIPTION, DELIVERY, CHANNEL_STATE]


@dataclass(frozen=True)
class NotificationKind:
    """Something a module tells people about, e.g. "Waiting for your approval"."""

    name: str  # unique in the module; the record is named <module>.<name>
    label: str | dict[str, str]
    description: str | dict[str, str] | None = None
    # Channels besides the app it goes to unless people choose: ("telegram",) for urgent ones.
    channels: tuple[str, ...] = ()
