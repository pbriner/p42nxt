"""Places: where things are, as one tree: sites hold buildings, buildings floors or rooms,
floors rooms, rooms racks. Other modules point to a place (a device's location, a rental, a
task); a rack knows its height in units, so devices take their position in it.
"""

from pathlib import Path
from typing import Any

from p42cloud.sdk import (
    Change,
    Choice,
    Client,
    FieldError,
    FormSection,
    FormView,
    Grant,
    Integer,
    Item,
    ListView,
    Module,
    Problem,
    Reference,
    RelatedList,
    Role,
    Section,
    String,
    Table,
    TableHook,
    Text,
    Widget,
    package_version,
)

ALL = ("read", "create", "write", "delete", "restore")
KINDS = {
    "site": "Site",
    "building": "Building",
    "floor": "Floor",
    "room": "Room",
    "rack": "Rack",
    "other": "Other",
}
# What each kind may hold; "other" fits anywhere and holds anything.
HOLDS = {
    None: {"site", "other"},
    "site": {"building", "other"},
    "building": {"floor", "room", "other"},
    "floor": {"room", "other"},
    "room": {"rack", "other"},
    "rack": {"other"},
    "other": set(KINDS),
}
MAX_UNITS = 60

PLACE = Table(
    "place",
    "Place",
    {
        "name": String("Name", max_length=120, required=True),
        "kind": Choice("Kind", KINDS, required=True, default="room"),
        "parent": Reference("Part of", "places.place", on_delete="restrict"),
        "code": String("Code", max_length=40),
        "street": String("Street", max_length=160),
        "postal_code": String("Postal code", max_length=20),
        "city": String("City", max_length=100),
        "country": String("Country", max_length=2),
        "responsible": Reference("Responsible", "sys.person"),
        # Racks: their height in units (U), for the devices' positions in them.
        "units": Integer("Height (units)", help="Racks only: usually 42."),
        "notes": Text("Notes"),
    },
    label_plural="Places",
    display="name",
    parent="parent",
)

READER = Role(
    "places.reader",
    "Places reader",
    grants=(Grant("places.place", ("read",)),),
    description="Sees every place.",
)
MANAGER = Role(
    "places.manager",
    "Places manager",
    grants=(Grant("places.place", ALL),),
    includes=("places.reader",),
    description="Records sites, buildings, floors, rooms and racks.",
)


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(field, "invalid", message)],
    )


async def _check_place(change: Change) -> None:
    """Sensible nesting (a room in a building, not a building in a room), a rack's units."""
    if change.operation in ("delete", "restore"):
        return
    client = Client.of(change)
    current: dict[str, Any] = (
        await client.get("places.place", change.sys_id)
        if change.operation != "create" and change.sys_id is not None
        else {}
    )
    merged = current | change.values
    kind = str(merged.get("kind") or "room")
    parent_kind = None
    if merged.get("parent"):
        parent_kind = str((await client.get("places.place", str(merged["parent"])))["kind"])
    if kind not in HOLDS[parent_kind]:
        where = KINDS[parent_kind].lower() if parent_kind else "the top"
        raise _invalid("parent", f"A {KINDS[kind].lower()} doesn't go in {where}.")
    units = merged.get("units")
    if kind == "rack":
        if units is None:
            change.values["units"] = 42
        elif not 1 <= int(units) <= MAX_UNITS:
            raise _invalid("units", f"From 1 to {MAX_UNITS} units.")
    elif units is not None:
        raise _invalid("units", "Only racks have a height in units.")


module = Module(
    id="places",
    name="Places",
    publisher="p42",
    version=package_version("p42cloud-places"),
    depends_on={"sys": ">=0.51,<1"},
    tables=(PLACE,),
    roles=(READER, MANAGER),
    hooks=(TableHook("places.place", before=_check_place),),
    docs=Path(__file__).parent / "docs",
    translations=Path(__file__).parent / "translations",
    description="Sites, buildings, floors, rooms and racks, as one tree.",
    category="Places",
    icon="location_city",
    views=(
        ListView(
            "places.place",
            ("name", "kind", "parent", "code", "city", "responsible"),
            sort=("name",),
            search=("name", "code", "city"),
        ),
    ),
    forms=(
        FormView(
            "places.place",
            (
                FormSection("Place", ("name", "kind", "parent", "code", "responsible", "units")),
                FormSection("Address", ("street", "postal_code", "city", "country")),
                FormSection("Notes", ("notes",), columns=1),
            ),
            related=(RelatedList("places.place", "parent"),),
        ),
    ),
    navigation=(
        Section(
            "Places",
            (
                Item(
                    "dashboard",
                    "Dashboard",
                    kind="page",
                    path="/dashboard/places",
                    icon="dashboard",
                ),
                Item("places", "Places", "places.place", icon="location_city"),
                Item(
                    "sites",
                    "Sites",
                    "places.place",
                    filter={"field": "kind", "op": "eq", "value": "site"},
                    icon="domain",
                ),
                Item(
                    "racks",
                    "Racks",
                    "places.place",
                    filter={"field": "kind", "op": "eq", "value": "rack"},
                    icon="dns",
                ),
                Item("new_place", "New place", "places.place", kind="create", icon="add"),
            ),
        ),
    ),
    widgets=(
        Widget(
            "by_kind",
            "Places by kind",
            "chart",
            dashboard="places",
            list="places.place",
            group="kind",
            chart="bar",
            icon="location_city",
        ),
    ),
)
