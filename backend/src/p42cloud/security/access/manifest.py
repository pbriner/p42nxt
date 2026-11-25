"""What access adds to the core: its roles, and the groups a new installation starts with."""

import json
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog
from p42cloud.data.engine_steps import DATABASE_PERSON
from p42cloud.kernel.context import SYSTEM_PERSON
from p42cloud.security.access.groups import recalculate_group
from p42cloud.security.access.models import OPERATIONS
from p42cloud.security.access.roles import Grant, Role

ALL = tuple(OPERATIONS)
PEOPLE = "sys.person"
ACCESS_TABLES = (
    "sys.group",
    "sys.membership",
    "sys.role",
    "sys.role_include",
    "sys.grant",
    "sys.group_role",
)
METADATA = ("sys.table", "sys.field", "sys.field_type")
SHARES = "sys.share"
SESSIONS = "sys.session"
# What people see of sessions: never the token's hash.
SESSION_FIELDS = ("person", "stage", "expires", "last_seen", "verified", "address", "agent")
TOKENS = "sys.api_token"
# What people see of API tokens: never the token's hash.
TOKEN_FIELDS = ("person", "name", "prefix", "roles", "addresses", "expires", "last_used")
SCREENS = (
    "sys.nav_item",
    "sys.list_view",
    "sys.form_view",
    "sys.ui_action",
    "sys.form_rule",
    "sys.theme",
    "sys.widget",
    "sys.connection_type",
)
PERSONAL = (
    "sys.preference",
    "sys.favorite",
    "sys.saved_view",
    "sys.view_state",
    "sys.dashboard_layout",
    "sys.related_layout",
    "sys.connection",
    "sys.connection_secret",
)
MINE = {"field": "owner", "op": "eq", "value": "@me"}
# Sessions and tokens are the person's own.
YOURS = {"field": "person", "op": "eq", "value": "@me"}

ROLES = [
    Role(
        "sys.admin",
        "Administrator",
        grants=(
            Grant(PEOPLE, ALL),
            *(Grant(t, ("read",)) for t in (*ACCESS_TABLES, *METADATA)),
            Grant(SESSIONS, ("read",), fields=SESSION_FIELDS),
            Grant(SESSIONS, ("delete",)),
            Grant(TOKENS, ("read",), fields=TOKEN_FIELDS),
            Grant(TOKENS, ("delete",)),
            Grant("sys.identity_provider", ALL),
            # Approvals: every request, approver and decision; the rules' steps and days.
            *(
                Grant(t, ("read",))
                for t in ("sys.approval", "sys.approver", "sys.approval_decision", "sys.deputy")
            ),
            Grant("sys.approval_rule", ("read",)),
            Grant(
                "sys.approval_rule",
                ("write",),
                fields=("steps", "remind_after", "escalate_after", "escalate_to"),
            ),
            Grant("sys.external_id", ("read",)),
            Grant("sys.module", ("read",)),
            Grant("sys.module_update", ("read",)),
            Grant("sys.update_request", ("read", "create", "write")),
            Grant("sys.backup", ("read",)),
            Grant("sys.setting", ("read", "write")),
            Grant("sys.invitation", ("read",), fields=("person", "kind", "expires", "used")),
            Grant("sys.theme", ALL),
            Grant("sys.widget_audience", ALL),
            Grant("sys.announcement", ALL),
            Grant("sys.language", ALL),
            Grant("sys.currency", ALL),
            Grant("sys.exchange_rate", ALL),
            Grant("sys.translation", ALL),
            Grant("sys.job", ("read",)),
            Grant("sys.schedule", ("read",)),
            Grant("sys.schedule", ("write",), fields=("cron", "time_zone", "active", "missed")),
            Grant("sys.job_run", ("read",)),
            Grant("sys.call_type", ("read",)),
            Grant("sys.outside_call", ("read",)),
            Grant("sys.event_type", ("read",)),
            Grant("sys.state_flow", ("read",)),
            Grant("sys.state_transition", ("read",)),
            Grant("sys.event_subscription", ("read",)),
            Grant("sys.event", ("read",)),
            Grant("sys.event_delivery", ("read",)),
            # Which comments there are, on which records (read through their records otherwise).
            Grant("sys.comment", ("read",)),
            # Which files there are; their contents only through their records' rights.
            Grant("sys.file", ("read",), fields=("name", "type", "size", "record_table", "about")),
            Grant("sys.notification_kind", ("read",)),
            Grant("sys.notification_kind", ("write",), fields=("channels",)),
            Grant("sys.notification_delivery", ("read",)),
            Grant("sys.channel_state", ("read",)),
            Grant("sys.module_route", ("read",)),
        ),
        includes=("sys.widget_editor",),
        powerful=True,
        description="Runs the platform: people, settings, modules.",
    ),
    Role(
        "sys.widget_editor",
        "Widget editor",
        grants=(
            Grant("sys.widget", ("read",)),
            # Widgets made in the app, in full; a module's: only how it's used.
            Grant(
                "sys.widget",
                ("create", "write", "delete", "restore"),
                condition={"field": "custom", "op": "eq", "value": True},
            ),
            Grant(
                "sys.widget",
                ("write",),
                condition={"field": "custom", "op": "eq", "value": False},
                fields=("shown", "position", "size"),
            ),
            Grant("sys.widget_audience", ALL),
            Grant("sys.list_view", ("read",)),
            Grant("sys.group", ("read",), fields=("name", "description")),
        ),
        description="Makes widgets from lists and decides who gets which widget.",
    ),
    Role(
        "sys.access_admin",
        "Access administrator",
        grants=(
            Grant(PEOPLE, ("read",)),
            *(Grant(t, ALL) for t in ACCESS_TABLES),
            # Custom roles are built from tables and fields.
            *(Grant(t, ("read",)) for t in METADATA),
        ),
        powerful=True,
        description="Manages groups, their members and roles, and custom roles.",
    ),
    Role(
        "sys.user",
        "User",
        grants=(
            Grant(PEOPLE, ("read",), fields=("name", "kind", "active")),
            # All of their own record.
            Grant(PEOPLE, ("read",), condition={"field": "sys_id", "op": "eq", "value": "@me"}),
            Grant("sys.group", ("read",), fields=("name", "description", "assignable")),
            Grant(SHARES, ALL, condition=MINE),
            Grant(SHARES, ("read",), condition={"field": "person", "op": "eq", "value": "@me"}),
            Grant("sys.language", ("read",)),
            # The currencies and their rates, for amounts anywhere.
            Grant("sys.currency", ("read",)),
            Grant("sys.exchange_rate", ("read",)),
            # Messages for everyone, or for a group they're in.
            Grant(
                "sys.announcement",
                ("read",),
                condition={
                    "or": [
                        {"field": "audience", "op": "empty"},
                        {"field": "audience", "op": "in", "value": "@my_groups"},
                    ]
                },
            ),
            Grant(SESSIONS, ("read",), condition=YOURS, fields=SESSION_FIELDS),
            Grant(SESSIONS, ("delete",), condition=YOURS),
            Grant(TOKENS, ("read",), condition=YOURS, fields=TOKEN_FIELDS),
            Grant(TOKENS, ("delete",), condition=YOURS),
            # Their own deputies; what waits for their approval; what they asked approval for.
            Grant("sys.deputy", ("read", "create", "write", "delete", "restore"), condition=YOURS),
            Grant("sys.approver", ("read",), condition=YOURS),
            Grant(
                "sys.approval",
                ("read",),
                condition={"field": "requested_by", "op": "eq", "value": "@me"},
            ),
            # The documentation, and which modules there are.
            Grant("sys.doc_page", ("read",)),
            Grant(
                "sys.setting", ("read",), condition={"field": "public", "op": "eq", "value": True}
            ),
            Grant(
                "sys.module",
                ("read",),
                fields=(
                    "name",
                    "label",
                    "version",
                    "description",
                    "category",
                    "icon",
                    "enabled",
                    "title",
                    "category_title",
                    "summary",
                ),
            ),
            # The outside calls made for them.
            Grant(
                "sys.outside_call",
                ("read",),
                condition={"field": "owner", "op": "eq", "value": "@me"},
            ),
            # Their notifications (personal: nobody else sees them): read, marked read, put away.
            Grant("sys.notification", ("read", "delete", "restore")),
            Grant("sys.notification", ("write",), fields=("read",)),
            # Their linked chats and what they get where; which kinds there are, and the bot.
            Grant("sys.person_channel", ("read", "create", "delete")),
            Grant("sys.person_channel", ("write",), fields=("full_text",)),
            Grant("sys.notification_subscription", ALL),
            Grant(
                "sys.notification_kind",
                ("read",),
                fields=("name", "label", "description", "channels"),
            ),
            Grant("sys.channel_state", ("read",), fields=("name", "bot")),
            # The screens: what there is to see, and each person's own choices (personal
            # tables: nobody else sees them).
            *(Grant(t, ("read",)) for t in SCREENS),
            *(Grant(t, ALL) for t in PERSONAL),
        ),
        description="Everyone: sees who's who and shares their own records.",
    ),
]

# Fixed IDs, the same in every installation.
ADMINISTRATORS = uuid.UUID("00000000-0000-7000-8000-000000000101")
EVERYONE = uuid.UUID("00000000-0000-7000-8000-000000000102")
STARTING_GROUPS = (
    (
        ADMINISTRATORS,
        "Administrators",
        "Run the platform and manage access.",
        None,
        ("sys.admin", "sys.access_admin"),
    ),
    (
        EVERYONE,
        "Everyone",
        "Every active employee and external person.",
        {"field": "kind", "op": "in", "value": ["employee", "external"]},
        ("sys.user",),
    ),
)


async def seed(conn: AsyncConnection, catalog: Catalog) -> None:
    """The system's own people and, on a new installation, the starting groups.

    Each group is made once: later it may be renamed, or its roles moved to other groups.
    """
    for person, name in ((SYSTEM_PERSON, "System"), (DATABASE_PERSON, "Database")):
        await conn.execute(
            text(
                "INSERT INTO sys.person (sys_id, name, kind, active, emergency_access)"
                " VALUES (:id, :name, 'service', true, false)"
                " ON CONFLICT (sys_id) DO NOTHING"
            ),
            {"id": person, "name": name},
        )
    for group, name, description, rule, roles in STARTING_GROUPS:
        made = await conn.scalar(
            text(
                'INSERT INTO sys."group" (sys_id, name, description, assignable, rule)'
                " VALUES (:id, :name, :description, false, CAST(:rule AS jsonb))"
                " ON CONFLICT (sys_id) DO NOTHING RETURNING sys_id"
            ),
            {
                "id": group,
                "name": name,
                "description": description,
                "rule": json.dumps(rule) if rule else None,
            },
        )
        if made is None:
            continue
        await conn.execute(
            text(
                'INSERT INTO sys.group_role ("group", role)'
                " SELECT :group, sys_id FROM sys.role WHERE name = ANY(:roles)"
            ),
            {"group": group, "roles": list(roles)},
        )
        await recalculate_group(conn, catalog, group)
