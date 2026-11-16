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
    Job,
    JobRun,
    ListView,
    Module,
    Problem,
    Reference,
    Role,
    Route,
    RouteRequest,
    Schedule,
    Section,
    String,
    Table,
    TableHook,
    UIAction,
    Widget,
    package_version,
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
    files=True,
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

# For hello's own jobs (their service account gets the module's roles): no person needs it.
KEEPER = Role(
    "hello.keeper",
    "Greetings keeper",
    grants=(Grant("hello.greeting", ("read", "write")),),
    description="Tidies up greetings: for hello's jobs.",
)

OLD_PIN = {
    "and": [
        {"field": "pinned", "op": "eq", "value": True},
        {"field": "created", "op": "lt", "value": "@today-30d"},
    ]
}


async def _before_saving(change: Change) -> None:
    """A greeting says something, and its author is whoever leaves it."""
    text = change.values.get("text")
    if isinstance(text, str):
        if not text.strip():
            raise Problem(422, "empty_greeting", "Say something.")
        change.values["text"] = text.strip()
    if change.operation == "create":
        change.values["author"] = change.person


async def _unpin_old(run: JobRun) -> str:
    """Greetings pinned more than 30 days ago are unpinned: a job running every night."""
    client = Client(run.records)
    found = await client.list("hello.greeting", where=OLD_PIN, limit=500)
    for greeting in found["records"]:
        await client.update(
            "hello.greeting", greeting["sys_id"], {"pinned": False}, greeting["_etag"]
        )
    return f"{len(found['records'])} unpinned."


async def _sign(run: ActionRun) -> ActionResult:
    """Adds a signature to the greeting: an action that runs the module's own code."""
    client = Client(run.records)
    signed = f"{run.record['text']} — {run.inputs['signature']}"[:200]
    await client.update("hello.greeting", run.sys_id, {"text": signed}, run.record["_etag"])
    return ActionResult(message=f"Signed by {run.inputs['signature']}.")


LANGUAGES = {"en": "English", "de": "German", "fr": "French"}


async def _greetings(request: RouteRequest) -> list[dict[str, object]]:
    found = await Client.of(request).list("hello.greeting", limit=500)
    return found["records"]


async def _languages(request: RouteRequest) -> dict[str, object]:
    """A source widget's figure: how many languages people greet in (a list can't count that)."""
    counts: dict[str, int] = {}
    for greeting in await _greetings(request):
        language = str(greeting.get("language") or "en")
        counts[language] = counts.get(language, 0) + 1
    if not counts:
        return {"value": 0, "detail": "No greetings yet."}
    top = max(counts, key=lambda k: counts[k])
    return {
        "value": len(counts),
        "unit": "languages" if len(counts) != 1 else "language",
        "detail": f"Most greetings in {LANGUAGES.get(top, top)}.",
        "link": "/list/hello.greeting",
    }


async def _words(request: RouteRequest) -> dict[str, object]:
    """A source widget's chart: the average words per greeting in each language."""
    words: dict[str, list[int]] = {}
    for greeting in await _greetings(request):
        language = str(greeting.get("language") or "en")
        words.setdefault(language, []).append(len(str(greeting.get("text") or "").split()))
    return {
        "subtitle": "Average words per greeting",
        "points": [
            {"key": k, "label": LANGUAGES.get(k, k), "value": round(sum(v) / len(v), 1)}
            for k, v in sorted(words.items())
        ],
    }


module = Module(
    id="hello",
    name="Hello",
    publisher="p42",
    version=package_version("p42cloud-hello"),
    depends_on={"sys": ">=0.40,<1"},
    tables=(GREETING,),
    roles=(USER, KEEPER),
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
            "languages",
            "Languages",
            "figure",
            list="hello.greeting",
            source="/widget/languages",
            dashboard="hello",
            icon="translate",
            accent=3,
        ),
        Widget(
            "words",
            "Words per greeting",
            "chart",
            list="hello.greeting",
            source="/widget/words",
            chart="bar",
            dashboard="hello",
            sizes=("s", "wide", "large"),
            accent=5,
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
    routes=(
        Route("GET", "/widget/languages", _languages, "How many languages people greet in."),
        Route("GET", "/widget/words", _words, "Average words per greeting, by language."),
    ),
    jobs=(Job("unpin_old", "Unpin old greetings", _unpin_old, timeout=60),),
    schedules=(Schedule("unpin_old", "Old pins, nightly", "unpin_old", "41 4 * * *"),),
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
