"""Form views: declared or made from fields, checked; and the related lists a person sees."""

import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.catalog import load_catalog
from p42cloud.data.presenting import describe
from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.security.access.manifest import ADMINISTRATORS
from p42cloud.ui.forms.models import FormSection, FormView, RelatedList
from p42cloud.ui.forms.service import write_forms
from tests.security.helpers import acting


@pytest.fixture(scope="module")
async def engine(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    made = create_engine(upgraded.database_url)
    yield made
    await made.dispose()


async def test_every_core_table_has_a_form(engine: AsyncEngine) -> None:
    async with acting(engine, None) as records:
        forms = await records.list("sys.form_view", limit=500)
    by_table = {r.values["table"]: r.values for r in forms.records}
    core = {k for k, t in records.catalog.tables.items() if t.schema == "sys" and not t.retired}
    assert core - set(by_table) == set()
    (section,) = by_table["sys.person"]["sections"]
    assert section["fields"][0] == "name" and section["columns"] == 2


@pytest.mark.parametrize(
    ("form", "code"),
    [
        (FormView("sys.person", (FormSection("Main", ("colour",)),)), "unknown_field"),
        (FormView("sys.person", (FormSection("Main", ("name",), columns=9),)), "bad_columns"),
        (
            FormView(
                "sys.person", (FormSection("Main", ("name",)),), (RelatedList("sys.group", "name"),)
            ),
            "bad_related",
        ),
        (FormView("hello.greeting", (FormSection("Main", ("text",)),)), "unknown_table"),
    ],
)
async def test_forms_are_checked(owner_engine: AsyncEngine, form: FormView, code: str) -> None:
    with working_as(Context(channel="upgrade")), pytest.raises(Problem) as refused:
        async with owner_transaction(owner_engine) as conn:
            await write_forms(conn, await load_catalog(conn), "sys", (form,))
    assert code in [e.code for e in refused.value.errors]


async def test_related_lists_are_those_one_may_read(engine: AsyncEngine) -> None:
    tag = uuid.uuid4().hex[:6]
    async with acting(engine, None) as records:
        admin = (await records.create("sys.person", {"name": f"Admin {tag}"})).sys_id
        await records.create("sys.membership", {"group": ADMINISTRATORS, "person": admin})
        plain = (await records.create("sys.person", {"name": f"Plain {tag}"})).sys_id
    found: dict[str, set[str]] = {}
    for name, person in (("admin", admin), ("plain", plain)):
        async with acting(engine, person) as records:
            table = records.catalog.tables["sys.person"]
            described = describe(table, records.access, "en", records.catalog)
        found[name] = {f"{r['table']}.{r['field']}" for r in described["referenced_by"]}
    assert "sys.membership.person" in found["admin"]
    assert "sys.membership.person" not in found["plain"]  # they may not read memberships
