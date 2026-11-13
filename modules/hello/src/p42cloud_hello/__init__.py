"""Hello: the reference module. Everything a module needs, built on the SDK alone.

People leave greetings for everyone to read; each greeting's author may change or delete it.
"""

from pathlib import Path

from p42cloud.sdk import (
    Change,
    Choice,
    Grant,
    Module,
    Problem,
    Reference,
    Role,
    String,
    Table,
    TableHook,
)

GREETING = Table(
    "greeting",
    "Greeting",
    {
        "text": String("Text", max_length=200, required=True),
        "language": Choice(
            "Language", {"en": "English", "de": "German", "fr": "French"}, default="en"
        ),
        "author": Reference("Author", "sys.person"),
    },
    label_plural="Greetings",
)

MINE = {"field": "author", "op": "eq", "value": "@me"}

USER = Role(
    "hello.user",
    "Greeter",
    grants=(
        Grant("hello.greeting", ("read", "create")),
        Grant("hello.greeting", ("write", "delete"), condition=MINE),
    ),
    description="Reads every greeting and leaves their own.",
)


async def _before_saving(change: Change) -> None:
    """A greeting says something, and its author is whoever leaves it."""
    text = change.values.get("text")
    if isinstance(text, str):
        if not text.strip():
            raise Problem(422, "empty_greeting", "Say something.")
        change.values["text"] = text.strip()
    if change.operation == "create":
        change.values["author"] = change.person


module = Module(
    id="hello",
    name="Hello",
    version="0.1.0",
    depends_on={"sys": ">=0.1,<1"},
    tables=(GREETING,),
    roles=(USER,),
    hooks=(TableHook("hello.greeting", before=_before_saving),),
    docs=Path(__file__).parent / "docs",
    description="The reference module: greetings.",
)
