"""What the current piece of work is: who does it, through which channel, under which request ID.

Set once per request, job run or command, and read wherever it's needed (the database passes it
to the audit trigger). Lower layers read it; only entry points set it.
"""

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

# Who works when no person does: installing, upgrading, jobs. Fixed IDs, the same in every
# installation, so the audit log can name them.
SYSTEM_PERSON = uuid.UUID("00000000-0000-7000-8000-000000000001")


@dataclass(frozen=True)
class Context:
    channel: str  # ui, api, api:token:<name>, job:<id>, install, cli …
    person_id: uuid.UUID = SYSTEM_PERSON
    language: str = "en"
    request_id: uuid.UUID = field(default_factory=uuid.uuid7)


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
