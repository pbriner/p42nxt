"""Hierarchies: a table names each record's parent; the database keeps it a tree; filters find
what's under a record; records are named by their path."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.metadata import version
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.declarations import Table, check
from p42cloud.data.presenting import PATH_SEPARATOR, annotate
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.errors import Problem
from p42cloud.modules.core import CORE
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import Module
from tests.security.helpers import acting, change

SEP = PATH_SEPARATOR


def _module(module_id: str) -> Module:
    center = Table(
        "center",
        "Center",
        {
            "name": String("Name", max_length=60, required=True),
            "parent": Reference("Part of", f"{module_id}.center"),
        },
        parent="parent",
    )
    cost = Table(
        "cost",
        "Cost",
        {
            "text": String("Text", max_length=60),
            "center": Reference("Center", f"{module_id}.center"),
        },
    )
    return Module(
        id=module_id,
        publisher="test",
        name="Trees",
        version="1.0.0",
        depends_on={"sys": f">={version('p42cloud')}"},
        tables=(center, cost),
    )


@pytest.fixture(scope="module")
async def app(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def tree(
    owner_engine: AsyncEngine, new_module: Callable[[], Awaitable[str]], app: AsyncEngine
) -> dict[str, Any]:
    """A house with its flats and the heating under the ground floor; a garage beside it."""
    module_id = await new_module()
    with working_as(Context(channel="install")):
        await install_modules(owner_engine, [CORE, _module(module_id)])
    table = f"{module_id}.center"
    made: dict[str, Any] = {"module": module_id, "table": table}
    async with acting(app, None) as records:
        for name, parent in (
            ("House", None),
            ("Ground floor", "House"),
            ("Heating", "Ground floor"),
            ("Upper floor", "House"),
            ("Garage", None),
        ):
            values = {"name": name, "parent": made[parent] if parent else None}
            made[name] = (await records.create(table, values)).sys_id
    return made


async def _names(app: AsyncEngine, table: str, where: Any) -> set[str]:
    async with acting(app, None) as records:
        page = await records.list(table, where=where, limit=100)
    return {str(r.values.get("name") or r.values.get("text")) for r in page.records}


async def _refused(app: AsyncEngine, work: Callable[..., Awaitable[Any]]) -> list[str]:
    with pytest.raises(Problem) as refused:
        async with acting(app, None) as records:
            await work(records)
    return [e.code for e in refused.value.errors]


async def test_a_record_cant_be_under_itself_or_one_of_its_own(
    app: AsyncEngine, tree: dict[str, Any]
) -> None:
    table = tree["table"]

    async def own(records: Any) -> None:
        await change(records, table, tree["House"], {"parent": tree["Heating"]})

    async def itself(records: Any) -> None:
        await change(records, table, tree["Garage"], {"parent": tree["Garage"]})

    assert await _refused(app, own) == ["loop"]
    assert await _refused(app, itself) == ["loop"]
    async with acting(app, None) as records:  # elsewhere is fine
        await change(records, table, tree["Garage"], {"parent": tree["House"]})


async def test_a_record_with_records_under_it_isnt_removed(
    app: AsyncEngine, tree: dict[str, Any]
) -> None:
    table = tree["table"]

    async def remove(records: Any) -> None:
        current = await records.read(table, tree["Ground floor"])
        await records.delete(table, tree["Ground floor"], current.etag)

    assert await _refused(app, remove) == ["has_children"]
    async with acting(app, None) as records:
        await change(records, table, tree["Heating"], {"parent": tree["House"]})
    async with acting(app, None) as records:
        await remove(records)  # nothing under it any more

    async def under_removed(records: Any) -> None:
        await records.create(table, {"name": "Cellar", "parent": tree["Ground floor"]})

    assert await _refused(app, under_removed) == ["parent_removed"]


async def test_within_finds_a_record_and_everything_under_it(
    app: AsyncEngine, tree: dict[str, Any]
) -> None:
    table, cost = tree["table"], f"{tree['module']}.cost"
    under_house = {"field": "sys_id", "op": "within", "value": str(tree["House"])}
    assert await _names(app, table, under_house) == {
        "House",
        "Ground floor",
        "Heating",
        "Upper floor",
    }
    async with acting(app, None) as records:
        for text, center in (("Oil", "Heating"), ("Paint", "Upper floor"), ("Tyres", "Garage")):
            await records.create(cost, {"text": text, "center": tree[center]})
    assert await _names(
        app, cost, {"field": "center", "op": "within", "value": str(tree["Ground floor"])}
    ) == {"Oil"}
    assert await _names(
        app, cost, {"field": "center", "op": "within", "value": [str(tree["House"])]}
    ) == {"Oil", "Paint"}
    with pytest.raises(Problem) as not_a_tree:
        await _names(app, cost, {"field": "sys_id", "op": "within", "value": str(tree["House"])})
    assert [e.code for e in not_a_tree.value.errors] == ["not_a_tree"]


async def test_records_of_a_hierarchy_are_named_by_their_path(
    app: AsyncEngine, tree: dict[str, Any]
) -> None:
    table, cost = tree["table"], f"{tree['module']}.cost"
    async with acting(app, None) as records:
        made = await records.create(cost, {"text": "Oil", "center": tree["Heating"]})
        (body,) = await annotate(records, records.catalog.tables[cost], [made])
        heating = await records.read(table, tree["Heating"])
        (own,) = await annotate(records, records.catalog.tables[table], [heating])

    assert body["_display"]["center"] == f"House{SEP}Ground floor{SEP}Heating"
    assert own["_path"] == f"House{SEP}Ground floor{SEP}Heating"
    assert own["_display"]["parent"] == f"House{SEP}Ground floor"
    assert own["_display"]["name"] == own["_path"]  # lists show the path as its name
    assert own["name"] == "Heating"


@pytest.mark.parametrize(
    "parent",
    [
        String("Not a reference", max_length=10),
        Reference("Elsewhere", "zz.other"),
        Reference("Required", "zz.center", required=True),
    ],
)
def test_the_parent_is_an_optional_reference_to_the_same_table(parent: Any) -> None:
    table = Table(
        "center", "Center", {"name": String("Name", max_length=10), "up": parent}, parent="up"
    )
    with pytest.raises(Problem) as refused:
        check("zz", [table])
    assert [e.code for e in refused.value.errors] == ["bad_parent"]
