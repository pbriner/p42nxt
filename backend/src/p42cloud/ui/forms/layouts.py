"""Each person's related lists on a form: which, in what order, and the tab open last.

A personal record per person and form, written through the records API. Lists the person may
not read are simply not shown, whatever the record says.
"""

from typing import Any, cast

from p42cloud.data.declarations import Table
from p42cloud.data.hooks import Change, TableHook
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.kernel.errors import FieldError, Problem

RELATED_LAYOUT = Table(
    "related_layout",
    "Related lists layout",
    {
        "person": Reference("Person", "sys.person", required=True),
        "form": String("Form name", max_length=120, required=True),
        "form_ref": Reference("Form", "sys.form_view", key="form"),
        # [{"table": "sys.membership", "field": "group"}], in tab order; empty: as the default
        # (the person only switched tabs so far).
        "shown": Json("Shown lists"),
        "last": String("Open last", max_length=200),
    },
    label_plural="Related lists layouts",
    owner="person",
    unique_together=(("person", "form"),),
)

MAX_LISTS = 50


def _invalid(message: str) -> Problem:
    return Problem(
        422,
        "validation",
        "Some fields aren't valid.",
        errors=[FieldError("shown", "invalid", message)],
    )


async def _check(change: Change) -> None:
    if change.operation in ("delete", "restore"):
        return
    if change.operation == "create":
        change.values["person"] = change.person
    shown: Any = change.values.get("shown")
    if shown is None:
        return
    if not isinstance(shown, list) or len(cast(list[Any], shown)) > MAX_LISTS:
        raise _invalid(f"A list of at most {MAX_LISTS} related lists.")
    seen: set[tuple[str, str]] = set()
    for entry in cast(list[Any], shown):
        if not isinstance(entry, dict):
            raise _invalid('Each is {"table": …, "field": …}.')
        item = cast(dict[str, Any], entry)
        key = (item.get("table"), item.get("field"))
        if not all(isinstance(k, str) and 0 < len(k) <= 120 for k in key) or len(item) != 2:  # noqa: PLR2004
            raise _invalid('Each is {"table": …, "field": …}.')
        if key in seen:
            raise _invalid("A list is there twice.")
        seen.add(cast(tuple[str, str], key))


TABLE_HOOKS = (TableHook("sys.related_layout", before=_check),)
