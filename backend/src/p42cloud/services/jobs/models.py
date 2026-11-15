"""Jobs, schedules and job runs as records, and how modules declare them."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from p42cloud.data.declarations import Table
from p42cloud.data.records import Records
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.datetime import DateTime
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText

JOB = Table(
    "job",
    "Job",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "label": TranslatedText("Label", required=True),
        "timeout": Integer("Time limit (seconds)", required=True, default=300),
        "retries": Integer("Tries again", required=True, default=2),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Jobs",
    display="label",
)

SCHEDULE = Table(
    "schedule",
    "Schedule",
    {
        "name": String("Name", max_length=120, required=True, unique=True),
        "module": String("Module", max_length=16, required=True),
        "label": TranslatedText("Label", required=True),
        # The job by its name (the key modules use), and as a link to its record.
        "job": String("Job name", max_length=120, required=True),
        "job_ref": Reference("Job", "sys.job"),
        # Administrators may change when it runs, or pause it; upgrades keep what they set.
        "cron": String("When (cron)", max_length=100, required=True),
        "time_zone": String("Time zone", max_length=64, required=True, default="UTC"),
        "missed": Choice(
            "Missed runs",
            {"skip": "Skipped", "run_once": "Run once, late"},
            required=True,
            default="skip",
        ),
        "active": Boolean("Active", required=True, default=True),
        "next_run": DateTime("Next run"),
        "last_state": Choice(
            "Last run",
            {"succeeded": "Succeeded", "failed": "Failed", "skipped": "Skipped"},
            tones={"succeeded": "primary", "failed": "error", "skipped": "muted"},
        ),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="Schedules",
    display="label",
)

STATES = {
    "queued": "Queued",
    "running": "Running",
    "succeeded": "Succeeded",
    "failed": "Failed",
    "skipped": "Skipped",
}

JOB_RUN = Table(
    "job_run",
    "Job run",
    {
        # By their names (the keys the worker uses), and as links to their records.
        "job": String("Job name", max_length=120, required=True),
        "job_ref": Reference("Job", "sys.job"),
        "schedule": String("Schedule name", max_length=120),
        "schedule_ref": Reference("Schedule", "sys.schedule"),
        "state": Choice(
            "State",
            STATES,
            required=True,
            default="queued",
            tones={
                "running": "tertiary",
                "succeeded": "primary",
                "failed": "error",
                "skipped": "muted",
            },
        ),
        "trigger": Choice(
            "Started by",
            {"schedule": "Its schedule", "manual": "Someone", "retry": "A retry"},
            required=True,
            default="schedule",
        ),
        "attempt": Integer("Attempt", required=True, default=1),
        "not_before": DateTime("Not before"),
        "started": DateTime("Started"),
        "finished": DateTime("Finished"),
        "message": Text("Result"),
        "worker": String("Worker", max_length=120),
    },
    label_plural="Job runs",
    display="job",
)

TABLES = [JOB, SCHEDULE, JOB_RUN]


@dataclass(frozen=True)
class JobRun:
    """One run of a job, for the job's code: its records are the module's, as its service account
    (core jobs: the system's), in one transaction committed when the job returns."""

    records: Records
    run: UUID
    job: str
    attempt: int
    now: datetime


type Work = Callable[[JobRun], Awaitable[str | None]]


@dataclass(frozen=True)
class Job:
    """Work a module can run: on a schedule, or when someone starts it."""

    name: str  # unique in the module; the record is named <module>.<name>
    label: str | dict[str, str]
    run: Work
    timeout: int = 300  # seconds
    retries: int = 2  # tries again after failing, waiting longer each time


@dataclass(frozen=True)
class Schedule:
    """When one of the module's jobs runs (cron, in a time zone)."""

    name: str
    label: str | dict[str, str]
    job: str  # one of the module's jobs, by its name in the module
    cron: str
    time_zone: str = "UTC"
    missed: Literal["skip", "run_once"] = "skip"
