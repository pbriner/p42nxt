"""Events as records: the types modules publish, their subscriptions, and each delivery."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from p42cloud.data.declarations import Table
from p42cloud.data.records import Records
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText

EVENT_TYPE = Table(
    "event_type",
    "Event type",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "label": TranslatedText("Label", required=True),
        "description": TranslatedText("Description"),
        # The kind of record its events are about, if any.
        "about": String("About table", max_length=80),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Event types",
    display="label",
)

SUBSCRIPTION = Table(
    "event_subscription",
    "Event subscription",
    {
        "name": String("Name", max_length=160, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "label": TranslatedText("Label", required=True),
        # By name: a module may listen for a type whose module isn't installed (yet).
        "event_type": String("Event type name", max_length=120, required=True),
        "event_type_ref": Reference("Event type", "sys.event_type", key="event_type"),
        "retries": Integer("Tries again", required=True, default=4),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Event subscriptions",
    display="label",
)

EVENT = Table(
    "event",
    "Event",
    {
        "type": String("Type name", max_length=120, required=True),
        "type_ref": Reference("Type", "sys.event_type", key="type"),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "record_table": String("About table", max_length=80),
        "record_id": String("About record", max_length=36),
        "about": AnyRecord("About", table_field="record_table", read_only=True),
        "data": Json("Data"),
    },
    label_plural="Events",
    display="type",
)

STATES = {
    "pending": "Waiting",
    "working": "Working",
    "done": "Done",
    "failed": "Failed",
    "skipped": "Skipped",
}

DELIVERY = Table(
    "event_delivery",
    "Event delivery",
    {
        "event": Reference("Event", "sys.event", required=True),
        "subscription": String("Subscription name", max_length=160, required=True),
        "subscription_ref": Reference("Subscription", "sys.event_subscription", key="subscription"),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="pending",
            tones={
                "done": "primary",
                "failed": "error",
                "working": "tertiary",
                "skipped": "tertiary",
            },
        ),
        "attempts": Integer("Attempts", required=True, default=0),
        "not_before": DateTime("Not before"),
        "started": DateTime("Last started"),
        "finished": DateTime("Finished"),
        "message": Text("Last said"),
    },
    label_plural="Event deliveries",
    display="subscription",
)

TABLES = [EVENT_TYPE, SUBSCRIPTION, EVENT, DELIVERY]


@dataclass(frozen=True)
class EventType:
    """Something a module tells other modules about, after it happened."""

    name: str  # unique in the module; the record is named <module>.<name>
    label: str | dict[str, str]
    about: str | None = None  # the table its events are about, as "<module>.<table>"
    description: str | dict[str, str] | None = None


@dataclass(frozen=True)
class EventRun:
    """One delivery of an event, for the subscribing module's code: its records are the
    module's, as its service account (the core's: the system's), in one transaction that also
    marks the delivery done."""

    records: Records
    event: UUID
    type: str
    data: dict[str, Any]
    about_table: str | None
    about_id: UUID | None
    published: datetime
    published_by: UUID | None
    attempt: int


type Handler = Callable[[EventRun], Awaitable[None]]


@dataclass(frozen=True)
class Subscription:
    """What a module does when an event of a type is published (any module's type, by its full
    name `<module>.<name>`). Each event reaches it once: its work and the delivery's end are
    saved together; if it fails, nothing of it is kept and it's tried again later."""

    event: str
    handler: Handler
    label: str | dict[str, str] | None = None
    retries: int = 4
