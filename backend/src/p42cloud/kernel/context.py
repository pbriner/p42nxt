"""What the current piece of work is: who does it, through which channel, under which request ID,
and what they may do.

Set once per request, job run or command, and read wherever it's needed (the database passes it
to the audit trigger, the records service applies the access). Lower layers read it; only entry
points set it. The access is worked out by `security`; without one, nothing is allowed.
"""

import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Who works when no person does: installing, upgrading, jobs. Fixed IDs, the same in every
# installation, so the audit log can name them.
SYSTEM_PERSON = uuid.UUID("00000000-0000-7000-8000-000000000001")


@dataclass(frozen=True)
class Permit:
    """One grant as it applies to a person: which records, and which of their fields."""

    condition: Any = None  # in the filter language; None: every record
    fields: frozenset[str] | None = None  # None: every field


@dataclass(frozen=True)
class Access:
    """What a person may do, per table and operation (read, create, write, delete, restore)."""

    groups: frozenset[uuid.UUID] = frozenset()
    permits: Mapping[tuple[str, str], tuple[Permit, ...]] = field(
        default_factory=dict[tuple[str, str], tuple[Permit, ...]]
    )
    unrestricted: bool = False  # the system itself: installing, upgrading, its own jobs

    def of(self, table: str, operation: str) -> tuple[Permit, ...]:
        """The permits for an operation on a table; empty: not allowed at all."""
        if self.unrestricted:
            return (Permit(),)
        return self.permits.get((table, operation), ())


NOTHING = Access()
EVERYTHING = Access(unrestricted=True)


@dataclass(frozen=True)
class Context:
    channel: str  # ui, api, api:token:<name>, job:<id>, install, cli …
    person_id: uuid.UUID = SYSTEM_PERSON
    language: str = "en"
    request_id: uuid.UUID = field(default_factory=uuid.uuid7)
    access: Access = NOTHING  # deny by default
    # When the person last gave their second factor in this session; changes to access need it
    # to be recent.
    verified_at: datetime | None = None


_current: ContextVar[Context | None] = ContextVar("p42_context", default=None)


def current() -> Context:
    context = _current.get()
    if context is None:
        raise RuntimeError("no context: entry points must set one with `working_as`")
    return context


@contextmanager
def working_as(context: Context) -> Generator[Context]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
