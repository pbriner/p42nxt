"""Hello: the reference module. Everything a module needs, built on the SDK alone.

People leave greetings for everyone (or one team) to read; each greeting's author may change,
pin, sign or delete it.
"""

from pathlib import Path

from p42cloud.sdk import (
    ActionInput,
    ActionResult,
    ActionRun,
    Boolean,
    Change,
    Choice,
    Client,
    FormRule,
    FormSection,
    FormView,
    Grant,
    Item,
    ListView,
    Module,
    Problem,
    Reference,
    Role,
    Section,
    String,
    Table,
    TableHook,
    UIAction,
    Widget,
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
        "pinned": Boolean("Pinned", required=True, default=False),
        "audience": Choice(
            "For",
            {"everyone": "Everyone", "team": "One team"},
            required=True,
            default="everyone",
        ),
        "team": Reference("Team", "sys.group"),
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


async def _sign(run: ActionRun) -> ActionResult:
    """Adds a signature to the greeting: an action that runs the module's own code."""
    client = Client(run.records)
    signed = f"{run.record['text']} — {run.inputs['signature']}"[:200]
    await client.update("hello.greeting", run.sys_id, {"text": signed}, run.record["_etag"])
    return ActionResult(message=f"Signed by {run.inputs['signature']}.")


module = Module(
    id="hello",
    name="Hello",
    version="0.4.0",
    depends_on={"sys": ">=0.11,<1"},
    tables=(GREETING,),
    roles=(USER,),
    hooks=(TableHook("hello.greeting", before=_before_saving),),
    docs=Path(__file__).parent / "docs",
    description="The reference module: greetings.",
    category="Examples",
    icon="waving_hand",
    views=(ListView("hello.greeting", ("text", "language", "author", "pinned", "created")),),
    forms=(
        FormView(
            "hello.greeting",
            (
                FormSection("Greeting", ("text", "language", "pinned")),
                FormSection("Who reads it", ("audience", "team", "author")),
            ),
        ),
    ),
    actions=(
        UIAction(
            "pin",
            "hello.greeting",
            "Pin",
            places=("form_button", "list_bulk", "list_row"),
            icon="push_pin",
            condition={"field": "pinned", "op": "eq", "value": False},
            set={"pinned": True},
            message="Pinned.",
        ),
        UIAction(
            "unpin",
            "hello.greeting",
            "Unpin",
            places=("form_menu", "list_bulk", "list_row"),
            icon="keep_off",
            condition={"field": "pinned", "op": "eq", "value": True},
            set={"pinned": False},
            message="Unpinned.",
        ),
        UIAction(
            "sign",
            "hello.greeting",
            "Sign",
            places=("form_menu",),
            icon="signature",
            confirm="Add your signature to the greeting?",
            inputs=(ActionInput("signature", "Signature", options={"max_length": 40}),),
            handler=_sign,
        ),
    ),
    rules=(
        FormRule(
            "team_only_for_teams",
            "hello.greeting",
            condition={"field": "audience", "op": "eq", "value": "everyone"},
            hide=("team",),
        ),
        FormRule(
            "team_needed",
            "hello.greeting",
            condition={"field": "audience", "op": "eq", "value": "team"},
            required=("team",),
        ),
        FormRule("author_is_set", "hello.greeting", read_only=("author",)),
    ),
    widgets=(
        Widget(
            "greetings",
            "Greetings",
            "figure",
            list="hello.greeting",
            icon="chat",
            accent=7,
            trend="created",
            target=50,
        ),
        Widget(
            "pinned",
            "Pinned greetings",
            "list",
            list="hello.greeting",
            filter={"field": "pinned", "op": "eq", "value": True},
            dashboard="hello",
            sizes=("wide", "large"),
            size="wide",
        ),
        Widget(
            "by_language",
            "By language",
            "chart",
            list="hello.greeting",
            group="language",
            chart="donut",
            dashboard="hello",
            sizes=("s", "wide", "large"),
        ),
        Widget(
            "per_month",
            "Per month",
            "chart",
            list="hello.greeting",
            group="created",
            period="month",
            chart="column",
            dashboard="hello",
            sizes=("wide", "large"),
            size="wide",
        ),
        Widget(
            "about",
            "About greetings",
            "text",
            text="Leave a greeting for everyone, or for one team. Pin the ones that matter.",
            dashboard="hello",
            filled=True,
            accent=1,
            shown="available",
        ),
    ),
    navigation=(
        Section(
            "Greetings",
            (
                Item(
                    "dashboard", "Dashboard", kind="page", path="/dashboard/hello", icon="dashboard"
                ),
                Item("greetings", "Greetings", "hello.greeting", icon="chat"),
                Item("greet", "Leave a greeting", "hello.greeting", kind="create", icon="add"),
            ),
        ),
    ),
)
