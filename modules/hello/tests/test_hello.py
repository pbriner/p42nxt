"""Hello, tested in its own folder through the SDK alone."""

import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from p42cloud.sdk import Client, Problem

from p42cloud_hello import module

Acting = Callable[..., AbstractAsyncContextManager[Client]]


async def _greeters(as_system: Acting, *names: str) -> list[str]:
    """People holding the Greeter role, through a group of their own."""
    async with as_system() as client:
        role = await client.list(
            "sys.role", where={"field": "name", "op": "eq", "value": "hello.user"}
        )
        group = await client.create("sys.group", {"name": f"Greeters {uuid.uuid4().hex[:6]}"})
        await client.create(
            "sys.group_role", {"group": group["sys_id"], "role": role["records"][0]["sys_id"]}
        )
        people: list[str] = []
        for name in names:
            person = await client.create("sys.person", {"name": name})
            await client.create(
                "sys.membership", {"group": group["sys_id"], "person": person["sys_id"]}
            )
            people.append(person["sys_id"])
    return people


async def test_the_module_is_installed_with_its_documentation(as_system: Acting) -> None:
    async with as_system() as client:
        modules = await client.list(
            "sys.module", where={"field": "name", "op": "eq", "value": "hello"}
        )
        pages = await client.list(
            "sys.doc_page", where={"field": "module", "op": "eq", "value": "hello"}
        )
    assert modules["records"][0]["version"] == module.version
    assert [p["title"] for p in pages["records"]] == ["Hello"]


async def test_a_greeting_belongs_to_whoever_leaves_it(
    as_system: Acting, as_person: Acting
) -> None:
    anna, bob = await _greeters(as_system, "Anna", "Bob")
    async with as_person(anna) as client:
        made = await client.create("hello.greeting", {"text": "  Hi all  ", "author": bob})
    assert made["author"] == anna  # not whoever she named
    assert made["text"] == "Hi all"
    assert made["language"] == "en"


async def test_only_the_author_changes_a_greeting(as_system: Acting, as_person: Acting) -> None:
    anna, bob = await _greeters(as_system, "Anna", "Bob")
    async with as_person(anna) as client:
        made = await client.create("hello.greeting", {"text": "Mine"})
    async with as_person(bob) as client:
        seen = await client.get("hello.greeting", made["sys_id"])
        with pytest.raises(Problem) as refused:
            await client.update("hello.greeting", made["sys_id"], {"text": "Ours"}, seen["_etag"])
    async with as_person(anna) as client:
        changed = await client.update(
            "hello.greeting", made["sys_id"], {"text": "Still mine"}, seen["_etag"]
        )
    assert refused.value.status == 403
    assert changed["text"] == "Still mine"


@pytest.mark.parametrize("text", ["", "   "])
async def test_a_greeting_says_something(as_system: Acting, as_person: Acting, text: str) -> None:
    (anna,) = await _greeters(as_system, "Anna")
    with pytest.raises(Problem) as refused:
        async with as_person(anna) as client:
            await client.create("hello.greeting", {"text": text})
    assert refused.value.status == 422


async def test_without_the_role_there_are_no_greetings(
    as_system: Acting, as_person: Acting
) -> None:
    async with as_system() as client:
        someone: dict[str, Any] = await client.create("sys.person", {"name": "Someone"})
    with pytest.raises(Problem) as refused:
        async with as_person(someone["sys_id"]) as client:
            await client.list("hello.greeting")
    assert refused.value.status == 403


async def test_greetings_are_in_the_navigator(as_system: Acting) -> None:
    async with as_system() as client:
        items = await client.list(
            "sys.nav_item", where={"field": "module", "op": "eq", "value": "hello"}
        )
        views = await client.list(
            "sys.list_view", where={"field": "name", "op": "eq", "value": "hello.greeting"}
        )
    assert {r["name"] for r in items["records"]} == {"hello.greetings", "hello.greet"}
    assert views["records"][0]["columns"] == ["text", "language", "author", "pinned", "created"]


async def test_pin_and_unpin_are_offered_by_state(as_system: Acting, as_person: Acting) -> None:
    anna, bob = await _greeters(as_system, "Anna", "Bob")
    async with as_person(anna) as client:
        made = await client.create("hello.greeting", {"text": "Pin me"})
        pinned = await client.action("hello.greeting", made["sys_id"], "hello.pin")
        with pytest.raises(Problem) as again:
            await client.action("hello.greeting", made["sys_id"], "hello.pin")
        seen = await client.get("hello.greeting", made["sys_id"])
    async with as_person(bob) as client:
        with pytest.raises(Problem) as not_his:
            await client.action("hello.greeting", made["sys_id"], "hello.unpin")
    assert pinned["message"] == "Pinned." and seen["pinned"] is True
    assert again.value.code == "not_available"  # pinned already
    assert not_his.value.code == "not_available"  # not his to change


async def test_signing_runs_the_modules_code_with_its_input(
    as_system: Acting, as_person: Acting
) -> None:
    (anna,) = await _greeters(as_system, "Anna")
    async with as_person(anna) as client:
        made = await client.create("hello.greeting", {"text": "Hi"})
        with pytest.raises(Problem) as missing:
            await client.action("hello.greeting", made["sys_id"], "hello.sign")
        done = await client.action(
            "hello.greeting", made["sys_id"], "hello.sign", {"signature": "Anna"}
        )
        seen = await client.get("hello.greeting", made["sys_id"])
    assert missing.value.code == "validation"
    assert done["message"] == "Signed by Anna." and seen["text"] == "Hi — Anna"


async def test_a_team_greeting_needs_its_team(as_system: Acting, as_person: Acting) -> None:
    (anna,) = await _greeters(as_system, "Anna")
    async with as_system() as client:
        team = await client.create("sys.group", {"name": "Hello team"})
    with pytest.raises(Problem) as refused:
        async with as_person(anna) as client:
            await client.create("hello.greeting", {"text": "Team only", "audience": "team"})
    async with as_person(anna) as client:
        made = await client.create(
            "hello.greeting", {"text": "Team only", "audience": "team", "team": team["sys_id"]}
        )
    assert [e.field for e in refused.value.errors] == ["team"]
    assert made["team"] == team["sys_id"]
