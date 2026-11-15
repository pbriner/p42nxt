"""Preferences: one personal record per person, made the first time they change something."""

from typing import Any

from sqlalchemy import text

from p42cloud.data.declarations import Table
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.integer import Integer
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.themes.models import THEME_NAME

PREFERENCE = Table(
    "preference",
    "Preferences",
    {
        "person": Reference("Person", "sys.person", required=True, unique=True),
        "mode": Choice(
            "Mode",
            {"system": "Like the system", "light": "Light", "dark": "Dark"},
            required=True,
            default="system",
        ),
        # Empty: the installation's default theme.
        "theme": String("Theme name", max_length=40),
        "theme_ref": Reference("Theme", "sys.theme", key="theme"),
        "page_size": Integer("Records per page", required=True, default=20),
        # Empty: the browser's language.
        "language": Choice("Language", {"en": "English", "de": "Deutsch"}),
    },
    label_plural="Preferences",
    owner="person",
)

# "All" is 5000: enough for any list worth reading on one page.
PAGE_SIZES = frozenset({10, 20, 30, 40, 50, 100, 500, 5000})


def _invalid(name: str, message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError(name, "invalid", message)],
    )


async def _check(change: Change) -> None:
    if change.operation == "create":
        change.values["person"] = change.person
    size: Any = change.values.get("page_size")
    if size is not None and size not in PAGE_SIZES:
        raise _invalid("page_size", "One of 10, 20, 30, 40, 50, 100, 500 or 5000.")
    theme = change.values.get("theme")
    if theme is not None:
        known = (
            isinstance(theme, str)
            and THEME_NAME.match(theme)
            and await change.records.conn.scalar(
                text("SELECT true FROM sys.theme WHERE name = :n AND deleted IS NULL"), {"n": theme}
            )
        )
        if not known:
            raise _invalid("theme", "No such theme.")


TABLE_HOOKS = (TableHook("sys.preference", before=_check),)
