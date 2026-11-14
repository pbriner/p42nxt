"""What the screens add to the core: their tables, hooks, settings, views, navigation, themes."""

from p42cloud.services.connections.service import check
from p42cloud.services.jobs.service import queue
from p42cloud.ui.actions.models import UI_ACTION, ActionResult, ActionRun, UIAction
from p42cloud.ui.favorites import models as favorites
from p42cloud.ui.forms import layouts
from p42cloud.ui.forms.models import FORM_VIEW
from p42cloud.ui.navigation.models import NAV_ITEM, Item, Section
from p42cloud.ui.preferences import models as preferences
from p42cloud.ui.rules import service as rules
from p42cloud.ui.rules.models import FORM_RULE
from p42cloud.ui.shell import APP_NAME, LANGUAGE, STAGE
from p42cloud.ui.themes import builtin
from p42cloud.ui.themes import service as themes
from p42cloud.ui.themes.models import THEME
from p42cloud.ui.views import models as views
from p42cloud.ui.views import service as view_service
from p42cloud.ui.views.models import ListView
from p42cloud.ui.widgets import models as widgets
from p42cloud.ui.widgets import service as widget_service
from p42cloud.ui.widgets.models import Widget

TABLES = (
    NAV_ITEM,
    *views.TABLES,
    FORM_VIEW,
    layouts.RELATED_LAYOUT,
    UI_ACTION,
    FORM_RULE,
    *widgets.TABLES,
    THEME,
    preferences.PREFERENCE,
    favorites.FAVORITE,
)
HOOKS = (
    *view_service.TABLE_HOOKS,
    *themes.TABLE_HOOKS,
    *preferences.TABLE_HOOKS,
    *favorites.TABLE_HOOKS,
    *rules.TABLE_HOOKS,
    *widget_service.TABLE_HOOKS,
    *layouts.TABLE_HOOKS,
)
SETTINGS = (STAGE, APP_NAME, LANGUAGE, themes.DEFAULT_THEME)
THEMES = builtin.THEMES

# The core's lists worth finding in the search; its other tables get made, unsearched views.
VIEWS = (
    ListView("sys.person", ("name", "email", "kind", "active"), search=("name", "email")),
    ListView("sys.group", ("name", "description", "assignable")),
    ListView("sys.role", ("label", "name", "module", "powerful"), search=("label", "name")),
    ListView("sys.module", ("label", "name", "version", "enabled"), sort=("label",)),
    ListView(
        "sys.schedule",
        ("label", "cron", "time_zone", "active", "next_run", "last_state"),
        sort=("label",),
        searchable=False,
    ),
    ListView(
        "sys.connection",
        ("label", "type", "base_url", "health", "checked"),
        sort=("label",),
        searchable=False,
    ),
    ListView(
        "sys.outside_call",
        ("call", "owner", "state", "attempts", "sent", "message"),
        sort=("-created",),
        searchable=False,
    ),
    ListView("sys.connection_secret", ("name", "connection", "updated"), searchable=False),
    ListView(
        "sys.job_run",
        ("job", "state", "trigger", "attempt", "started", "finished", "message"),
        sort=("-created",),
        searchable=False,
    ),
)

NAVIGATION = (
    Section(
        "People and access",
        (
            Item("people", "People", "sys.person", icon="person", keywords=("users",)),
            Item("groups", "Groups", "sys.group", icon="group"),
            Item("roles", "Roles", "sys.role", icon="badge", keywords=("permissions",)),
            Item("providers", "Sign-in providers", "sys.identity_provider", icon="login"),
            Item("tokens", "API tokens", "sys.api_token", icon="key"),
        ),
    ),
    Section(
        "Modules and data",
        (
            Item("modules", "Modules", "sys.module", icon="extension"),
            Item("updates", "Available updates", "sys.module_update", icon="update"),
            Item("tables", "Tables", "sys.table", icon="table"),
            Item("settings", "Settings", "sys.setting", icon="tune"),
        ),
    ),
    Section(
        "Automation",
        (
            Item("schedules", "Schedules", "sys.schedule", icon="schedule", keywords=("cron",)),
            Item("job_runs", "Job runs", "sys.job_run", icon="history", keywords=("jobs",)),
            Item(
                "outside_calls",
                "Outside calls",
                "sys.outside_call",
                icon="send",
                keywords=("orders", "api"),
            ),
        ),
    ),
    Section(
        "Connections",
        (
            Item(
                "connections",
                "Connections",
                "sys.connection",
                icon="cable",
                keywords=("api", "keys", "integrations"),
            ),
        ),
    ),
    Section(
        "Appearance",
        (Item("themes", "Themes", "sys.theme", icon="palette", keywords=("colours", "colors")),),
    ),
)

# On everyone's home, for those who may read people.
WIDGETS = (
    Widget(
        "people",
        "People",
        "figure",
        list="sys.person",
        filter={"field": "active", "op": "eq", "value": True},
        icon="group",
        accent=1,
        trend="created",
        shown="available",
    ),
)


async def _run_now(run: ActionRun) -> ActionResult:
    """Queues a run of the schedule's job, for the next free worker."""
    await queue(run.records.conn, str(run.record["job"]), str(run.record["name"]), "manual")
    return ActionResult(message="Queued: it starts as soon as a worker is free.")


async def _check(run: ActionRun) -> ActionResult:
    """Whether the connection works: the type's check, through the safe client."""
    return ActionResult(message=await check(run.records, run.sys_id))


ACTIONS = (
    UIAction(
        "check",
        "sys.connection",
        "Check",
        places=("form_button", "list_row"),
        icon="cable",
        handler=_check,
    ),
    UIAction(
        "run_now",
        "sys.schedule",
        "Run now",
        places=("form_button", "list_row"),
        icon="play_arrow",
        handler=_run_now,
    ),
    UIAction(
        "pause",
        "sys.schedule",
        "Pause",
        places=("form_menu", "list_bulk", "list_row"),
        icon="pause",
        condition={"field": "active", "op": "eq", "value": True},
        set={"active": False},
        message="Paused.",
    ),
    UIAction(
        "resume",
        "sys.schedule",
        "Resume",
        places=("form_button", "list_bulk", "list_row"),
        icon="resume",
        condition={"field": "active", "op": "eq", "value": False},
        set={"active": True},
        message="Resumed.",
    ),
)
