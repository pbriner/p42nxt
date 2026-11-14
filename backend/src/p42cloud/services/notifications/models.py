"""Notification kinds and each person's notifications as records, and how modules declare kinds."""

from dataclasses import dataclass

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText

SEVERITIES = {"info": "Information", "success": "Success", "warning": "Warning", "error": "Problem"}

NOTIFICATION_KIND = Table(
    "notification_kind",
    "Notification kind",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "label": TranslatedText("Label", required=True),
        "description": TranslatedText("Description"),
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
        "kind": String("Kind", max_length=120, required=True),
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

TABLES = [NOTIFICATION_KIND, NOTIFICATION]


@dataclass(frozen=True)
class NotificationKind:
    """Something a module tells people about, e.g. "Waiting for your approval"."""

    name: str  # unique in the module; the record is named <module>.<name>
    label: str | dict[str, str]
    description: str | dict[str, str] | None = None
