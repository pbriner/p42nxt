"""Outside calls as records, and how modules declare them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx

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

CALL_TYPE = Table(
    "call_type",
    "Outside call type",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "label": TranslatedText("Label", required=True),
        "connection_type": String("Connection type name", max_length=120, required=True),
        "connection_type_ref": Reference(
            "Connection type", "sys.connection_type", key="connection_type"
        ),
        "retries": Integer("Tries again", required=True, default=3),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Outside call types",
    display="label",
)

STATES = {
    "pending": "Waiting",
    "sending": "Sending",
    "unknown": "Answer lost",
    "answered": "Answered",
    "done": "Done",
    "failed": "Failed",
}

OUTSIDE_CALL = Table(
    "outside_call",
    "Outside call",
    {
        "call": String("Call name", max_length=120, required=True),
        "call_ref": Reference("Call", "sys.call_type", key="call"),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        # For whom: their connection is used, and they see their calls.
        "owner": Reference("For", "sys.person", required=True),
        # Unique: the outside system is asked by it whether the call happened.
        "key": String("Key", max_length=64, required=True, unique=True),
        "payload": Json("What it sends"),
        "record_table": String("About table", max_length=80),
        "record_id": String("About record", max_length=36),
        # The same, as a link: the record's form lists its calls.
        "about": AnyRecord("About", table_field="record_table", read_only=True),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="pending",
            tones={
                "done": "primary",
                "failed": "error",
                "unknown": "tertiary",
                "sending": "tertiary",
            },
        ),
        "attempts": Integer("Attempts", required=True, default=0),
        "not_before": DateTime("Not before"),
        "sent": DateTime("Last sent"),
        "answer": Json("Answer"),
        "message": Text("Last said"),
    },
    label_plural="Outside calls",
    display="call",
)

TABLES = [CALL_TYPE, OUTSIDE_CALL]


class Refused(Exception):
    """The outside system said no, for good: the call fails and isn't tried again."""


@dataclass(frozen=True)
class CallRun:
    """One delivery of a call, for the module's code."""

    key: str  # send it along so the outside system can be asked later (Kraken: userref)
    payload: dict[str, Any]
    owner: UUID
    record_table: str | None
    record_id: str | None
    attempt: int
    now: datetime


type Send = Callable[[httpx.AsyncClient, CallRun], Awaitable[dict[str, Any]]]
type Reconcile = Callable[[httpx.AsyncClient, CallRun], Awaitable[dict[str, Any] | None]]
type Done = Callable[[Records, CallRun, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class GaveUp:
    """Why a call ends without an answer: `refused` (the other side said no for good),
    `unclear` (it may have happened: tried as often as allowed, answers lost), or `not_sent`
    (it never reached the other side)."""

    why: Literal["refused", "unclear", "not_sent"]
    message: str


type GiveUp = Callable[[Records, CallRun, GaveUp], Awaitable[None]]


@dataclass(frozen=True)
class OutsideCall:
    """A change in an outside system, made safely: recorded with the module's change, sent by
    the worker through a connection of the module's type, and reconciled if its answer was lost.

    `send` makes the call and returns the answer; it raises `Refused` when the other side says
    no for good. `reconcile` asks the other side, by the call's key, whether it happened: the
    answer if it did, None if it didn't. `done` reacts to the answer in a transaction (as the
    module's service account), for example by updating the record the call is about;
    `gave_up` reacts when it ends without one (refused, or still unclear after the last try).
    """

    name: str  # unique in the module; the record is named <module>.<name>
    label: str | dict[str, str]
    connection_type: str  # one of the module's connection types, by its name in the module
    send: Send
    reconcile: Reconcile
    done: Done | None = None
    retries: int = 3
    gave_up: GiveUp | None = None  # reacts when the call ends without an answer
