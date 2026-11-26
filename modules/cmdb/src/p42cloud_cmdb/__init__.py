"""CMDB: the configuration items a company runs, from a rack to a container, their relations,
and maps of what needs what. Each class is its own table; the index `cmdb.ci` gives every item
one ID. Devices found by UniFi and Proxmox go to a review list; Shelly devices are read and
switched; certificates warn before they expire; the installation registers itself.
"""

import json
from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    EVERY_TABLE,
    ActionInput,
    EventType,
    FormSection,
    FormView,
    Grant,
    Invalid,
    Item,
    Job,
    ListView,
    Migration,
    Module,
    NotificationKind,
    RelatedList,
    Role,
    Route,
    Schedule,
    Section,
    Setting,
    Subscription,
    TableHook,
    UIAction,
    Widget,
    package_version,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud_cmdb import discovery, network
from p42cloud_cmdb.classes import BY_KEY, CLASSES, FAMILIES, TABLES, CiClass
from p42cloud_cmdb.index import (
    check_class,
    check_index,
    check_relationship,
    check_type,
    custom_after,
    impact_route,
    index_after,
    map_route,
)

ALL = ("read", "create", "write", "delete", "restore")
CLASS_TABLES = tuple(f"cmdb.{c.key}" for c in CLASSES)
OWN_TABLES = (
    "cmdb.ci",
    "cmdb.relationship",
    "cmdb.interface",
    "cmdb.port",
    "cmdb.cable",
    "cmdb.review",
    *CLASS_TABLES,
)

# (key, label, reverse, meaning, both ways, from classes, to classes)
RELATION_TYPES: tuple[tuple[str, str, str, str, bool, list[str], list[str]], ...] = (
    (
        "runs_on",
        "runs on",
        "hosts",
        "needs",
        False,
        ["vm", "container_engine", "container", "application", "agent"],
        ["computer", "server", "vm", "container_engine", "storage"],
    ),
    (
        "hosted_on",
        "hosted on",
        "hosts",
        "needs",
        False,
        ["application", "business_application"],
        ["container", "vm", "server", "computer"],
    ),
    ("part_of", "part of", "has part", "needs", False, [], []),
    ("contains", "contains", "is in", "link", False, ["container_stack"], []),
    ("secured_by", "secured by", "secures", "needs", False, [], ["certificate"]),
    ("uses", "uses", "used by", "needs", False, [], []),
    ("depends_on", "depends on", "needed by", "needs", False, [], []),
    ("connected_to", "connected to", "connected to", "link", True, [], []),
    ("protected_by", "protected by", "protects", "needs", False, [], []),
)


async def seed(conn: AsyncConnection, _: Any) -> None:
    """The shipped classes and relation types; a type a manager changed stays as they left it."""
    for c in CLASSES:
        await conn.execute(
            text(
                'INSERT INTO cmdb.ci_class (name, label, "table", family, icon, custom)'
                " VALUES (:n, :l, :t, :f, :i, false) ON CONFLICT (name) WHERE deleted IS NULL"
                ' DO UPDATE SET label = :l, "table" = :t, family = :f, icon = :i'
            ),
            {"n": c.key, "l": c.label, "t": f"cmdb.{c.key}", "f": c.family, "i": c.icon},
        )
    for key, label, reverse, meaning, both, froms, tos in RELATION_TYPES:
        values = {
            "n": key,
            "l": label,
            "r": reverse,
            "m": meaning,
            "b": both,
            "f": json.dumps(froms or None),
            "t": json.dumps(tos or None),
        }
        await conn.execute(
            text(
                "INSERT INTO cmdb.relation_type (name, label, reverse_label, meaning, both_ways,"
                " from_classes, to_classes, custom, changed) VALUES (:n, :l, :r, :m, :b,"
                " CAST(:f AS jsonb), CAST(:t AS jsonb), false, false)"
                " ON CONFLICT (name) WHERE deleted IS NULL DO UPDATE SET label = :l,"
                " reverse_label = :r, meaning = :m, both_ways = :b,"
                " from_classes = CAST(:f AS jsonb), to_classes = CAST(:t AS jsonb)"
                " WHERE NOT coalesce(cmdb.relation_type.changed, false)"
            ),
            values,
        )


async def repair_reviews(conn: AsyncConnection) -> None:
    """0.1.2 let people set a review row to "created" by hand, making nothing, and the button
    left the row without its item. A row whose item exists (made with its key) is linked to
    it; one with no item waits again, so it can be created. Every row gets its address, MAC
    and host name from its facts, so lists can search them."""
    await conn.execute(
        text(
            "UPDATE cmdb.review SET"
            " address = left(coalesce(facts->>'address', facts->>'management_address'), 64),"
            " mac = left(lower(facts->>'mac'), 32), host_name = left(facts->>'host_name', 253)"
            " WHERE deleted IS NULL"
        )
    )
    rows = (
        await conn.execute(
            text(
                "SELECT sys_id, match_key FROM cmdb.review"
                " WHERE state = 'created' AND ci IS NULL AND deleted IS NULL"
            )
        )
    ).all()
    for row in rows:
        ci = None
        for key in BY_KEY:
            ci = await conn.scalar(
                text(
                    # Only the shipped class tables' names go into the statement.
                    f"SELECT i.sys_id FROM cmdb.{key} r JOIN cmdb.ci i"  # noqa: S608
                    ' ON i.record = r.sys_id AND i."table" = :t'
                    " WHERE r.discovery_key = :k AND r.deleted IS NULL LIMIT 1"
                ),
                {"t": f"cmdb.{key}", "k": row.match_key},
            )
            if ci is not None:
                break
        if ci is not None:
            await conn.execute(
                text("UPDATE cmdb.review SET ci = :c WHERE sys_id = :i"),
                {"c": ci, "i": row.sys_id},
            )
        else:
            await conn.execute(
                text("UPDATE cmdb.review SET state = 'waiting' WHERE sys_id = :i"),
                {"i": row.sys_id},
            )


def _yes_no(given: Any) -> bool:
    if not isinstance(given, bool):
        raise Invalid("Yes or no.")
    return given


SELF_REGISTRATION = Setting(
    "cmdb.self_registration",
    "Register this installation in the CMDB",
    True,
    check=_yes_no,
    help="Its application, modules and database as configuration items, kept up to date.",
    public=True,
)

READER = Role(
    "cmdb.reader",
    "CMDB reader",
    grants=(
        *(Grant(t, ("read",)) for t in OWN_TABLES),
        Grant("cmdb.relation_type", ("read",)),
        Grant("cmdb.ci_class", ("read",)),
        # The core's agents, to show them as items (their answers stay with managers).
        Grant("sys.agent", ("read",)),
    ),
    includes=("places.reader", "partners.reader"),
    description="Sees every configuration item, relationship and map.",
)
EDITOR = Role(
    "cmdb.editor",
    "CMDB editor",
    grants=tuple(Grant(t, ALL) for t in OWN_TABLES),
    includes=("cmdb.reader",),
    description="Records configuration items and relationships, works the review list.",
)
OPERATOR = Role(
    "cmdb.operator",
    "CMDB operator",
    # Switching only: where a device is and who reaches it stay with editors.
    grants=(Grant("cmdb.shelly_device", ("write",), fields=("notes",)),),
    includes=("cmdb.reader",),
    description="Switches Shelly devices on and off.",
)
MANAGER = Role(
    "cmdb.manager",
    "CMDB manager",
    grants=(
        Grant("cmdb.relation_type", ALL),
        Grant("cmdb.ci_class", ALL),
        Grant("sys.agent_task", ("read",)),
    ),
    includes=("cmdb.editor", "cmdb.operator"),
    description="Everything, including relation types and classes of one's own.",
)

PLACE_INPUT = ActionInput("place", "Place", "reference", options={"table": "places.place"})


def _move(c: CiClass) -> UIAction:
    return UIAction(
        f"move_{c.key}",
        f"cmdb.{c.key}",
        "Move to a place",
        places=("list_bulk", "form_menu"),
        icon="move_down",
        inputs=(PLACE_INPUT,),
        set={"place": "@input.place"},
        message="Moved.",
    )


ACTIONS = (
    *(_move(c) for c in CLASSES),
    UIAction(
        "review_create",
        "cmdb.review",
        "Create the item",
        places=("form_button", "list_row", "list_bulk"),
        icon="add_circle",
        condition={"field": "state", "op": "eq", "value": "waiting"},
        inputs=(
            ActionInput(
                "ci_class",
                "Class",
                "choice",
                required=False,
                options={"values": [{"value": c.key, "label": c.label} for c in CLASSES]},
            ),
            ActionInput(
                "place", "Place", "reference", required=False, options={"table": "places.place"}
            ),
        ),
        handler=discovery.create_from_review,
    ),
    UIAction(
        "review_attach",
        "cmdb.review",
        "It's one we know",
        places=("form_button",),
        icon="link",
        condition={"field": "state", "op": "eq", "value": "waiting"},
        inputs=(ActionInput("ci", "Item", "reference", options={"table": "cmdb.ci"}),),
        handler=discovery.attach_review,
    ),
    UIAction(
        "review_ignore",
        "cmdb.review",
        "Ignore",
        places=("form_button", "list_row", "list_bulk"),
        icon="visibility_off",
        condition={"field": "state", "op": "eq", "value": "waiting"},
        set={"state": "ignored"},
        message="Ignored: it isn't offered again.",
    ),
    UIAction(
        "switch_on",
        "cmdb.shelly_device",
        "Switch on",
        places=("form_button", "list_row"),
        icon="toggle_on",
        confirm="Switch it on?",
        handler=discovery.switch_on,
    ),
    UIAction(
        "switch_off",
        "cmdb.shelly_device",
        "Switch off",
        places=("form_button", "list_row"),
        icon="toggle_off",
        confirm="Switch it off?",
        handler=discovery.switch_off,
    ),
    UIAction(
        "toggle",
        "cmdb.shelly_device",
        "Toggle",
        places=("form_menu",),
        icon="swap_horiz",
        confirm="Toggle it?",
        handler=discovery.toggle,
    ),
)


def _view(c: CiClass) -> ListView:
    first = ["name", *(["kind"] if c.kinds else []), "status", "place", "owner"]
    extra = [f for f in ("host_name", "model", "version", "valid_until", "online") if f in c.own]
    return ListView(f"cmdb.{c.key}", (*first, *extra[:2]), sort=("name",), search=("name",))


def _form(c: CiClass) -> FormView:
    basics = ("name", *(("kind",) if c.kinds else ()), "status", "environment", "criticality")
    return FormView(
        f"cmdb.{c.key}",
        (
            FormSection("Basics", basics),
            FormSection("Where and who", ("place", "owner", "support_group")),
            FormSection(c.label, tuple(c.own)),
            FormSection(
                "Purchase",
                (
                    "manufacturer",
                    "model",
                    "serial",
                    "asset_tag",
                    "supplier",
                    "bought_on",
                    "currency",
                    "price",
                    "warranty_until",
                ),
            ),
            FormSection("Discovery", ("discovery_key", "last_seen", "scanned")),
            FormSection("Notes", ("notes",), columns=1),
        ),
    )


def _items(family: str) -> tuple[Item, ...]:
    return tuple(
        Item(c.key, c.plural, f"cmdb.{c.key}", icon=c.icon) for c in CLASSES if c.family == family
    )


NAVIGATION = (
    Section(
        "CMDB",
        (
            Item("dashboard", "Dashboard", kind="page", path="/dashboard/cmdb", icon="dashboard"),
            Item("all", "All items", "cmdb.ci", icon="inventory_2"),
            Item("relationships", "Relationships", "cmdb.relationship", icon="hub"),
            Item(
                "review",
                "Review list",
                "cmdb.review",
                filter={"field": "state", "op": "eq", "value": "waiting"},
                icon="fact_check",
            ),
        ),
    ),
    *(Section(label, _items(family)) for family, label in FAMILIES.items()),
    Section(
        "Network",
        (
            Item("interfaces", "Network interfaces", "cmdb.interface", icon="settings_ethernet"),
            Item("ports", "Ports", "cmdb.port", icon="cable"),
            Item("cables", "Cables", "cmdb.cable", icon="cable"),
        ),
    ),
    Section(
        "CMDB settings",
        (
            Item("relation_types", "Relation types", "cmdb.relation_type", icon="schema"),
            Item("classes", "Classes", "cmdb.ci_class", icon="category"),
        ),
    ),
)

WIDGETS = (
    Widget(
        "map",
        "Map",
        "map",
        dashboard="cmdb",
        list="cmdb.ci",
        source="/map",
        param=("ci", "cmdb.ci"),
        icon="hub",
        sizes=("wide", "large"),
        size="large",
    ),
    Widget(
        "by_class",
        "Items by class",
        "chart",
        dashboard="cmdb",
        list="cmdb.ci",
        group="ci_class",
        chart="bar",
        icon="category",
    ),
    Widget(
        "certificates_expiring",
        "Certificates ending in 30 days",
        "figure",
        dashboard="cmdb",
        list="cmdb.certificate",
        filter={"field": "valid_until", "op": "lte", "value": "@today+30d"},
        icon="verified_user",
        accent=6,
    ),
    Widget(
        "review_waiting",
        "Found, to review",
        "figure",
        dashboard="cmdb",
        list="cmdb.review",
        filter={"field": "state", "op": "eq", "value": "waiting"},
        icon="fact_check",
        accent=2,
    ),
    Widget(
        "not_seen",
        "Not seen for 30 days",
        "figure",
        dashboard="cmdb",
        list="cmdb.ci",
        filter={"field": "last_seen", "op": "lt", "value": "@today-30d"},
        icon="visibility_off",
        accent=4,
    ),
)

EVENTS = (
    EventType("device_down", "A device stops answering"),
    EventType("device_recovered", "A device answers again"),
    EventType("certificate_expiring", "A certificate ends soon", about="cmdb.certificate"),
    EventType("certificate_expired", "A certificate has ended", about="cmdb.certificate"),
    EventType("review_waiting", "Something found waits for review"),
)

module = Module(
    id="cmdb",
    name="CMDB",
    publisher="p42",
    version=package_version("p42cloud-cmdb"),
    depends_on={"sys": ">=0.52.1,<1", "places": ">=0.1,<1", "partners": ">=0.1,<1"},
    tables=TABLES,
    roles=(READER, EDITOR, OPERATOR, MANAGER),
    hooks=(
        *(TableHook(t, after=index_after) for t in CLASS_TABLES),
        TableHook(EVERY_TABLE, after=custom_after),
        TableHook("cmdb.ci", before=check_index),
        TableHook("cmdb.ci_class", before=check_class),
        TableHook("cmdb.relation_type", before=check_type),
        TableHook("cmdb.relationship", before=check_relationship),
        TableHook("cmdb.certificate", before=discovery.read_certificate),
        TableHook("cmdb.shelly_device", before=discovery.check_address),
        TableHook("cmdb.cable", before=network.check_cable),
    ),
    seed=seed,
    subscriptions=(
        Subscription("sys.agent_enrolled", discovery.on_agent, label="Agents become items"),
        Subscription("sys.agent_quiet", discovery.on_agent, label="A quiet agent is down"),
        Subscription("sys.agent_back", discovery.on_agent, label="An agent is back"),
        Subscription(
            "sys.agent_task_done", discovery.on_agent_task, label="Shelly devices read by agents"
        ),
    ),
    settings=(SELF_REGISTRATION,),
    connection_types=discovery.CONNECTION_TYPES,
    events=EVENTS,
    notification_kinds=(
        NotificationKind(
            "certificate", "A certificate ends soon", "30, 14 and 3 days before, and once ended."
        ),
    ),
    jobs=(
        Job("register", "Register this installation", discovery.register, timeout=120),
        Job("unifi_scan", "Read the UniFi console", discovery.unifi_scan, timeout=300),
        Job("proxmox_scan", "Read Proxmox", discovery.proxmox_scan, timeout=300),
        Job("shelly_read", "Read Shelly devices", discovery.shelly_read, timeout=240),
        Job(
            "certificates",
            "Warn of ending certificates",
            discovery.certificates_expiring,
            timeout=120,
        ),
    ),
    schedules=(
        Schedule("register", "Register this installation, hourly", "register", "17 * * * *"),
        Schedule("unifi_scan", "UniFi, every 30 minutes", "unifi_scan", "*/30 * * * *"),
        Schedule("proxmox_scan", "Proxmox, every 30 minutes", "proxmox_scan", "7,37 * * * *"),
        Schedule("shelly_read", "Shelly devices, every 5 minutes", "shelly_read", "*/5 * * * *"),
        Schedule("certificates", "Ending certificates, daily", "certificates", "20 6 * * *"),
    ),
    routes=(
        Route(
            "GET", "/map", map_route, "The items linked to one, what it needs and what needs it."
        ),
        Route("GET", "/impact", impact_route, "If this is down: everything that needs it."),
        Route("GET", "/path", network.path_route, "From a port through panels to the far end."),
    ),
    actions=ACTIONS,
    migrations=(Migration("0.1.3", repair_reviews),),
    widgets=WIDGETS,
    views=(
        ListView(
            "cmdb.ci",
            ("number", "name", "ci_class", "status", "place", "owner", "last_seen"),
            sort=("name",),
            search=("name", "number"),
        ),
        ListView("cmdb.relationship", ("from_ci", "type", "to_ci", "origin"), sort=("from_ci",)),
        ListView(
            "cmdb.review",
            ("found", "address", "mac", "found_by", "ci_class_ref", "state", "seen"),
            sort=("-seen",),
            search=("found", "address", "mac", "host_name"),
        ),
        ListView(
            "cmdb.relation_type",
            ("label", "reverse_label", "meaning", "both_ways", "custom"),
            sort=("label",),
        ),
        ListView("cmdb.ci_class", ("label", "name", "family", "table", "custom"), sort=("label",)),
        ListView("cmdb.interface", ("ci", "name", "mac", "speed", "vlan"), sort=("ci",)),
        ListView("cmdb.port", ("ci", "name", "kind", "label"), sort=("ci",)),
        ListView(
            "cmdb.cable", ("label", "from_port", "to_port", "kind", "length_m"), sort=("label",)
        ),
        *(_view(c) for c in CLASSES),
    ),
    forms=(
        FormView(
            "cmdb.ci",
            (FormSection("Item", ("number", "name", "ci_class", "status", "place", "owner")),),
            related=(
                RelatedList("cmdb.relationship", "from_ci"),
                RelatedList("cmdb.relationship", "to_ci"),
                RelatedList("cmdb.interface", "ci"),
                RelatedList("cmdb.port", "ci"),
            ),
        ),
        *(_form(c) for c in CLASSES),
    ),
    navigation=NAVIGATION,
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Configuration items, their relations and maps; discovery and a review list.",
    category="IT",
    icon="hub",
)

__all__ = ["BY_KEY", "module"]
