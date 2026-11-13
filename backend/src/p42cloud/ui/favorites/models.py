"""Favorites and their folders: personal records, ordered by position."""

from sqlalchemy import text

from p42cloud.data.declarations import Table
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.errors import FieldError, Problem

FAVORITE = Table(
    "favorite",
    "Favorite",
    {
        "person": Reference("Person", "sys.person", required=True),
        "kind": Choice(
            "Kind", {"item": "Navigation item", "folder": "Folder"}, required=True, default="item"
        ),
        # A navigation item's name. One the person can't open any more stays, unshown.
        "item": String("Item", max_length=120),
        "label": String("Folder name", max_length=60),
        "parent": Reference("Folder", "sys.favorite"),
        "position": Integer("Position", required=True, default=0),
    },
    label_plural="Favorites",
    owner="person",
)

MAX_FAVORITES = 200


def _invalid(name: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(name, "invalid", message)],
    )


async def _current(change: Change) -> dict[str, object]:
    if change.sys_id is None:
        return {}
    row = (
        await change.records.conn.execute(
            text("SELECT kind, item, label, parent FROM sys.favorite WHERE sys_id = :id"),
            {"id": change.sys_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else {}  # pyright: ignore[reportPrivateUsage]


def _check_folder(change: Change, merged: dict[str, object]) -> None:
    label = " ".join(str(merged.get("label") or "").split())
    if not label:
        raise _invalid("label", "A folder needs a name.")
    if "label" in change.values:
        change.values["label"] = label
    if merged.get("item") or merged.get("parent"):
        raise _invalid("parent", "Folders hold favorites, not other folders.")


async def _check_item(change: Change, merged: dict[str, object]) -> None:
    conn = change.records.conn
    if not merged.get("item") or merged.get("label"):
        raise _invalid("item", "A favorite names a navigation item, and has no name of its own.")
    if "item" in change.values and not await conn.scalar(
        text("SELECT true FROM sys.nav_item WHERE name = :n AND deleted IS NULL"),
        {"n": change.values["item"]},
    ):
        raise _invalid("item", "No such navigation item.")
    parent = change.values.get("parent")
    if parent is not None and not await conn.scalar(
        text(
            "SELECT true FROM sys.favorite WHERE sys_id = :id AND person = :p"
            " AND kind = 'folder' AND deleted IS NULL"
        ),
        {"id": parent, "p": change.person},
    ):
        raise _invalid("parent", "Not one of your folders.")


async def _check(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["person"] = change.person
        count = await change.records.conn.scalar(
            text("SELECT count(*) FROM sys.favorite WHERE person = :p AND deleted IS NULL"),
            {"p": change.person},
        )
        if (count or 0) >= MAX_FAVORITES:
            raise Problem(409, "too_many", f"At most {MAX_FAVORITES} favorites.")
    elif "kind" in change.values:
        raise _invalid("kind", "A favorite stays what it is.")
    merged = await _current(change) | change.values
    if merged.get("kind") == "folder":
        _check_folder(change, merged)
    else:
        await _check_item(change, merged)


async def _empty_folder(change: Change) -> None:
    """A deleted folder's favorites move to the top level; nothing else is lost."""
    if change.operation != "delete":
        return
    await change.records.conn.execute(
        text("UPDATE sys.favorite SET parent = NULL WHERE parent = :id AND deleted IS NULL"),
        {"id": change.sys_id},
    )


TABLE_HOOKS = (TableHook("sys.favorite", before=_check, after=_empty_folder),)
