"""Agents, their one-time enrolment codes and their tasks, as records."""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text

AGENT = Table(
    "agent",
    "Agent",
    {
        "name": String("Name", max_length=80, required=True, unique=True),
        # Its own service account: what it may do is given to that account.
        "person": Reference("Acts as", "sys.person", read_only=True),
        "version": String("Version", max_length=40, read_only=True),
        "host": String("Host it runs on", max_length=253, read_only=True),
        "address": String("Seen from", max_length=64, read_only=True),
        "enrolled": DateTime("Enrolled", read_only=True),
        "last_seen": DateTime("Last seen", read_only=True),
        "quiet": Boolean("Quiet", read_only=True, default=False),
        "active": Boolean("Active", required=True, default=True),
        "notes": Text("Notes"),
    },
    label_plural="Agents",
    display="name",
)

AGENT_CODE = Table(
    "agent_code",
    "Agent enrolment code",
    {
        "name": String("For the agent", max_length=80, required=True),
        # Its hash; the code itself is shown once.
        "code": String("Code hash", max_length=64, required=True, unique=True),
        "expires": DateTime("Expires", required=True),
        "used": DateTime("Used"),
    },
    label_plural="Agent enrolment codes",
    display="name",
)

STATES = {
    "queued": "Waiting",
    "running": "Running",
    "done": "Done",
    "failed": "Failed",
}

AGENT_TASK = Table(
    "agent_task",
    "Agent task",
    {
        "agent": Reference("Agent", "sys.agent", required=True, on_delete="cascade"),
        "module": String("For module", max_length=16, required=True),
        "kind": String("What", max_length=60, required=True),
        "payload": Json("Asks"),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="queued",
            tones={"running": "primary", "failed": "error", "done": "muted"},
        ),
        "result": Json("Answered"),
        "started": DateTime("Started"),
        "finished": DateTime("Finished"),
    },
    label_plural="Agent tasks",
)

TABLES = [AGENT, AGENT_CODE, AGENT_TASK]
