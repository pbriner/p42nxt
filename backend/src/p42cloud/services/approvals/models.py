"""Approvals as records: the rules (from the flows, changeable by administrators), each request
with its approvers and decisions, and people's deputies."""

from dataclasses import dataclass

from p42cloud.data.declarations import Table
from p42cloud.data.types.any_record import AnyRecord
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.date import Date
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text

APPROVAL_RULE = Table(
    "approval_rule",
    "Approval rule",
    {
        # The transition's name: <module>.<transition>.
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module name", max_length=16, required=True),
        "module_ref": Reference("Module", "sys.module", key="module"),
        "transition": Reference("Move", "sys.state_transition", key="name", match="name"),
        # [{"role": "budget.controller", "field": "total", "above": "10000"}]: in order; a step
        # with field and above applies only when the record's field is above that.
        "steps": Json("Steps", required=True),
        "remind_after": Integer("Remind after (days)", default=3),
        "escalate_after": Integer("Escalate after (days)", default=7),
        # Who gets it when nobody decides in time: a role; empty: the administrators.
        "escalate_to": String("Escalate to (role)", max_length=120),
        "changed": Boolean(
            "Changed by an administrator",
            default=False,
            read_only=True,
            help="Kept as it is when the module is updated.",
        ),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Approval rules",
    display="name",
)

STATES = {
    "pending": "Waiting",
    "approved": "Approved",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
}

APPROVAL = Table(
    "approval",
    "Approval",
    {
        "title": String("What", max_length=255, required=True),
        "table": String("Table", max_length=80, required=True),
        "record": AnyRecord("Record", table_field="table", required=True),
        "rule": Reference("Rule", "sys.approval_rule", required=True),
        "transition": String("Move", max_length=120, required=True),
        "requested_by": Reference("Asked by", "sys.person", required=True),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="pending",
            tones={"pending": "primary", "rejected": "error", "withdrawn": "muted"},
        ),
        # The steps that apply to this record, as the rule said when it was asked.
        "steps": Json("Steps", required=True),
        "step": Integer("Step", required=True, default=0),
        "step_started": DateTime("Step started", required=True),
        "reminded": DateTime("Reminded"),
        "escalated": Boolean("Escalated", required=True, default=False),
        "decided": DateTime("Decided"),
        "note": Text("Why (rejected)"),
    },
    label_plural="Approvals",
    display="title",
)

APPROVER = Table(
    "approver",
    "Approver",
    {
        "approval": Reference("Approval", "sys.approval", required=True, on_delete="cascade"),
        "person": Reference("Approver", "sys.person", required=True),
        "step": Integer("Step", required=True),
        # A deputy approving for someone away.
        "on_behalf_of": Reference("For", "sys.person"),
        "escalation": Boolean("Escalated to them", required=True, default=False),
        "open": Boolean("Waiting for them", required=True, default=True),
    },
    label_plural="Approvers",
)

DECISIONS = {"approve": "Approved", "reject": "Rejected"}

APPROVAL_DECISION = Table(
    "approval_decision",
    "Approval decision",
    {
        "approval": Reference("Approval", "sys.approval", required=True, on_delete="cascade"),
        "step": Integer("Step", required=True),
        "person": Reference("By", "sys.person", required=True),
        "on_behalf_of": Reference("For", "sys.person"),
        "decision": Choice("Decision", DECISIONS, required=True),
        "note": Text("Note"),
    },
    label_plural="Approval decisions",
)

DEPUTY = Table(
    "deputy",
    "Deputy",
    {
        "person": Reference("Away", "sys.person", required=True, default="@me"),
        "deputy": Reference("Deputy", "sys.person", required=True),
        "starts": Date("From", required=True),
        "ends": Date("Until", required=True),
    },
    label_plural="Deputies",
)

TABLES = [APPROVAL_RULE, APPROVAL, APPROVER, APPROVAL_DECISION, DEPUTY]


@dataclass(frozen=True)
class Step:
    """One step of an approval: a yes from someone holding `role`. With `field` and `above`, only
    when the record's number field is above that value."""

    role: str
    field: str | None = None
    above: str | None = None


@dataclass(frozen=True)
class Approval:
    """A move that needs approval: its steps in order, reminders and escalation in days."""

    steps: tuple[Step, ...]
    remind_after: int | None = 3
    escalate_after: int | None = 7
    escalate_to: str | None = None  # a role; None: the administrators
