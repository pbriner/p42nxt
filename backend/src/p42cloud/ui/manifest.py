"""What the screens add to the core: their tables, hooks, settings, views, navigation, themes."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from p42cloud.data.settings import value
from p42cloud.kernel.db import switch_role
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.services.connections.service import check
from p42cloud.services.events.service import try_again
from p42cloud.services.jobs.service import queue
from p42cloud.services.update_requests.service import tonight
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
    ListView("sys.role", ("label", "name", "module_ref", "powerful"), search=("label", "name")),
    ListView("sys.module", ("title", "name", "version", "enabled"), sort=("label",)),
    ListView(
        "sys.announcement",
        ("subject", "kind", "audience", "deliver", "valid_from", "valid_until"),
        sort=("-valid_from",),
        search=("subject",),
    ),
    ListView("sys.language", ("name", "code", "offered"), sort=("code",)),
    ListView("sys.currency", ("name", "label", "decimals", "active"), sort=("name",)),
    ListView(
        "sys.exchange_rate",
        ("date", "currency", "base", "rate", "origin"),
        sort=("-date", "currency"),
        searchable=False,
    ),
    ListView(
        "sys.translation",
        ("key", "language", "text", "origin", "module"),
        sort=("key",),
        search=("key", "text"),
        # Searched in its own list only: in every language, they'd answer searches in another.
        searchable=False,
    ),
    ListView(
        "sys.update_request",
        ("target_ref", "version", "state", "not_before", "requested_by", "finished"),
        sort=("-created",),
        searchable=False,
    ),
    ListView(
        "sys.widget",
        ("title", "module_ref", "kind", "dashboard", "shown"),
        sort=("module", "position"),
        search=("title", "name"),
    ),
    ListView(
        "sys.schedule",
        ("label", "job_ref", "cron", "time_zone", "active", "next_run", "last_state"),
        sort=("label",),
        searchable=False,
    ),
    ListView(
        "sys.connection",
        ("label", "type_ref", "base_url", "health", "checked"),
        sort=("label",),
        searchable=False,
    ),
    ListView(
        "sys.outside_call",
        ("call_ref", "owner", "state", "attempts", "sent", "message"),
        sort=("-created",),
        searchable=False,
    ),
    ListView("sys.event_type", ("label", "name", "module_ref", "about"), sort=("name",)),
    ListView(
        "sys.event_subscription",
        ("label", "event_type_ref", "module_ref", "retries"),
        sort=("name",),
        searchable=False,
    ),
    ListView(
        "sys.event",
        ("type_ref", "about", "created", "created_by"),
        sort=("-created",),
        searchable=False,
    ),
    ListView(
        "sys.event_delivery",
        ("event", "subscription_ref", "state", "attempts", "started", "message"),
        sort=("-created",),
        searchable=False,
    ),
    ListView(
        "sys.notification",
        ("title", "severity", "read", "created"),
        sort=("-created",),
        search=("title", "message"),
    ),
    ListView("sys.notification_kind", ("label", "name", "module_ref", "channels"), sort=("label",)),
    ListView(
        "sys.notification_delivery",
        ("person", "channel", "state", "attempts", "sent", "message"),
        sort=("-created",),
        searchable=False,
    ),
    ListView("sys.person_channel", ("channel", "state", "name", "full_text"), searchable=False),
    ListView(
        "sys.notification_subscription",
        ("kind_ref", "channel", "on"),
        sort=("kind",),
        searchable=False,
    ),
    ListView("sys.connection_secret", ("name", "connection", "updated"), searchable=False),
    ListView(
        "sys.job_run",
        (
            "job_ref",
            "schedule_ref",
            "state",
            "trigger",
            "attempt",
            "started",
            "finished",
            "message",
        ),
        sort=("-created",),
        searchable=False,
    ),
)

NAVIGATION = (
    Section(
        "My work",
        (
            Item(
                "notifications",
                "Notifications",
                "sys.notification",
                icon="notifications",
                keywords=("inbox", "messages"),
            ),
            Item(
                "my_approvals",
                "My approvals",
                "sys.approver",
                kind="page",
                path="/approvals",
                icon="approval",
                keywords=("approve", "reject", "waiting"),
            ),
            Item(
                "deputies",
                "My deputies",
                "sys.deputy",
                filter={"field": "person", "op": "eq", "value": "@me"},
                icon="supervisor_account",
                keywords=("away", "holiday", "stand in"),
            ),
        ),
    ),
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
            Item("update_requests", "Updates", "sys.update_request", icon="history"),
            Item("languages", "Languages", "sys.language", icon="translate"),
            Item("translations", "Translations", "sys.translation", icon="g_translate"),
            Item("currencies", "Currencies", "sys.currency", icon="payments"),
            Item(
                "exchange_rates",
                "Exchange rates",
                "sys.exchange_rate",
                icon="currency_exchange",
                keywords=("fx", "rates"),
            ),
            Item("tables", "Tables", "sys.table", icon="table"),
            Item("settings", "Settings", "sys.setting", icon="tune"),
        ),
    ),
    Section(
        "Automation",
        (
            Item("schedules", "Schedules", "sys.schedule", icon="schedule", keywords=("cron",)),
            Item("job_runs", "Job runs", "sys.job_run", icon="history", keywords=("jobs",)),
            Item("backups", "Backups", "sys.backup", icon="backup", keywords=("restore", "dump")),
            Item(
                "outside_calls",
                "Outside calls",
                "sys.outside_call",
                icon="send",
                keywords=("orders", "api"),
            ),
            Item("events", "Events", "sys.event", icon="bolt", keywords=("subscriptions",)),
            Item(
                "event_deliveries",
                "Event deliveries",
                "sys.event_delivery",
                icon="call_split",
                keywords=("events", "failed"),
            ),
            Item("event_types", "Event types", "sys.event_type", icon="category"),
            Item("approvals", "Approvals", "sys.approval", icon="approval"),
            Item(
                "approval_rules",
                "Approval rules",
                "sys.approval_rule",
                icon="rule",
                keywords=("approvals", "steps", "thresholds"),
            ),
            Item(
                "notification_kinds", "Notification kinds", "sys.notification_kind", icon="campaign"
            ),
            Item(
                "notification_deliveries",
                "Notification deliveries",
                "sys.notification_delivery",
                icon="outgoing_mail",
                keywords=("telegram",),
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
        (
            Item("themes", "Themes", "sys.theme", icon="palette", keywords=("colours", "colors")),
            Item("widgets", "Widgets", "sys.widget", icon="widgets", keywords=("dashboard",)),
            Item(
                "announcements",
                "Messages",
                "sys.announcement",
                icon="campaign",
                keywords=("announcement", "banner", "notice"),
            ),
        ),
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
    Widget(
        "my_approvals",
        "Waiting for my approval",
        "figure",
        list="sys.approver",
        filter={
            "and": [
                {"field": "person", "op": "eq", "value": "@me"},
                {"field": "open", "op": "eq", "value": True},
            ]
        },
        icon="approval",
        accent=2,
    ),
)


async def _run_now(run: ActionRun) -> ActionResult:
    """Queues a run of the schedule's job, for the next free worker."""
    await queue(run.records.conn, str(run.record["job"]), str(run.record["name"]), "manual")
    return ActionResult(message="Queued: it starts as soon as a worker is free.")


async def _update(run: ActionRun, *, at_night: bool) -> ActionResult:
    """Asks the updater on the machine to update to this release (checked by the request's
    hook: administrators, the second factor again, one at a time)."""
    zone = str(await value(run.records.conn, "sys.time_zone") or "UTC")
    when = tonight(zone) if at_night else datetime.now(UTC)
    await run.records.create(
        "sys.update_request",
        {
            "target": run.record["module"],
            "version": run.record["available"],
            "not_before": when,
        },
    )
    said = f"tonight at {when.astimezone(ZoneInfo(zone)):%H:%M}" if at_night else "now"
    return ActionResult(
        message=f"Requested {said}: the updater on the machine starts it. Follow it under Updates."
    )


async def _update_now(run: ActionRun) -> ActionResult:
    return await _update(run, at_night=False)


async def _update_tonight(run: ActionRun) -> ActionResult:
    return await _update(run, at_night=True)


async def _try_again(run: ActionRun) -> ActionResult:
    await try_again(run.records.conn, run.sys_id)
    return ActionResult(message="It will be tried again shortly.")


async def _roles_to_administrators(run: ActionRun) -> ActionResult:
    """Gives the module's roles to the group Administrators, those it doesn't hold yet: a module
    just installed is usable at once. Through the records service, so only those who may give
    roles to groups can, and the rules against handing out more than one holds apply."""
    module = str(run.record["name"])
    conn = run.records.conn
    await switch_role(conn, "sys")
    roles = list(
        (
            await conn.execute(
                text(
                    "SELECT r.sys_id FROM sys.role r WHERE r.module = :m AND NOT r.custom"
                    " AND r.deleted IS NULL AND NOT EXISTS (SELECT 1 FROM sys.group_role gr"
                    ' WHERE gr."group" = :g AND gr.role = r.sys_id AND gr.deleted IS NULL)'
                ),
                {"m": module, "g": ADMINISTRATORS},
            )
        ).scalars()
    )
    for role in roles:
        await run.records.create("sys.group_role", {"group": ADMINISTRATORS, "role": role})
    if not roles:
        return ActionResult(message="Administrators hold all its roles already.")
    return ActionResult(message=f"Administrators hold its roles now ({len(roles)}).")


async def _check(run: ActionRun) -> ActionResult:
    """Whether the connection works: the type's check, through the safe client."""
    return ActionResult(message=await check(run.records, run.sys_id))


# What fits: an update of an installed module, or a module to install.
AN_UPDATE = {
    "and": [
        {"field": "fits", "op": "eq", "value": True},
        {"field": "kind", "op": "ne", "value": "new"},
    ]
}
A_NEW_MODULE = {
    "and": [
        {"field": "fits", "op": "eq", "value": True},
        {"field": "kind", "op": "eq", "value": "new"},
    ]
}

ACTIONS = (
    UIAction(
        "roles_to_administrators",
        "sys.module",
        "Give its roles to Administrators",
        places=("form_button",),
        icon="admin_panel_settings",
        operation="read",
        condition={"field": "name", "op": "ne", "value": "sys"},
        second_factor=True,
        confirm="Give the module's roles to the group Administrators?",
        handler=_roles_to_administrators,
    ),
    UIAction(
        "install_now",
        "sys.module_update",
        "Install now",
        places=("form_button", "list_row"),
        icon="add_circle",
        operation="read",
        condition=A_NEW_MODULE,
        second_factor=True,
        confirm="Install now? A backup is made first; the app is unavailable for a few minutes."
        " Afterwards, give the module's roles to groups.",
        handler=_update_now,
    ),
    UIAction(
        "install_tonight",
        "sys.module_update",
        "Install tonight",
        places=("form_button", "list_row"),
        icon="bedtime",
        operation="read",
        condition=A_NEW_MODULE,
        second_factor=True,
        handler=_update_tonight,
    ),
    UIAction(
        "try_again",
        "sys.event_delivery",
        "Try again",
        places=("form_button", "list_row"),
        icon="replay",
        # Administrators only read deliveries; this is the one change they may make to one.
        operation="read",
        condition={"field": "state", "op": "eq", "value": "failed"},
        handler=_try_again,
    ),
    UIAction(
        "update_now",
        "sys.module_update",
        "Update now",
        places=("form_button", "list_row"),
        icon="system_update_alt",
        operation="read",
        condition=AN_UPDATE,
        second_factor=True,
        confirm="Update now? A backup is made first; the app is unavailable for a few minutes.",
        handler=_update_now,
    ),
    UIAction(
        "update_tonight",
        "sys.module_update",
        "Update tonight",
        places=("form_button", "list_row"),
        icon="bedtime",
        operation="read",
        condition=AN_UPDATE,
        second_factor=True,
        handler=_update_tonight,
    ),
    UIAction(
        "mark_read",
        "sys.notification",
        "Mark as read",
        places=("form_button", "list_bulk", "list_row"),
        icon="mark_email_read",
        condition={"field": "read", "op": "eq", "value": False},
        set={"read": True},
        message="Marked as read.",
    ),
    UIAction(
        "mark_unread",
        "sys.notification",
        "Mark as unread",
        places=("form_menu", "list_bulk", "list_row"),
        icon="mark_email_unread",
        condition={"field": "read", "op": "eq", "value": True},
        set={"read": False},
        message="Marked as unread.",
    ),
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
