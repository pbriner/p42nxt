"""The tools an agent gets: the API's operations, each offered only for the tables the token's
grants allow, and run through the same services as the HTTP API (same rules, same audit)."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from p42cloud.data.aggregates import MAX_GROUPS, MEASURES, PERIODS, aggregate
from p42cloud.data.presenting import annotate, describe, translated
from p42cloud.data.records import Records
from p42cloud.kernel.errors import Problem
from p42cloud.ui.actions.service import run
from p42cloud.ui.search.service import search

type Json = dict[str, Any]
type Call = Callable[[Records, Json], Awaitable[Any]]

MAX_LIMIT = 100


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    operation: str | None  # the grant it needs on a table; None: no table
    properties: Json
    required: tuple[str, ...]
    call: Call
    read_only: bool = True
    destructive: bool = False


def _table(records: Records, given: Json, operation: str) -> str:
    key = str(given.get("table", ""))
    table = records.catalog.table(key)
    if table is None or table.switched_off or not records.access.of(key, operation):
        raise Problem(404, "no_table", "There's no such table for you.", detail=key)
    return key


def _id(given: Json) -> uuid.UUID:
    try:
        return uuid.UUID(str(given.get("id")))
    except ValueError as error:
        raise Problem(422, "validation", "`id` is a record's ID.") from error


async def _list_tables(records: Records, given: Json) -> Any:
    language = records.situation.language
    return [
        {
            "table": key,
            "label": translated(t.label_plural or t.label, language),
            "operations": [
                op for op in ("read", "create", "write", "delete") if records.access.of(key, op)
            ],
        }
        for key, t in sorted(records.catalog.tables.items())
        if not t.retired and not t.switched_off and records.access.of(key, "read")
    ]


async def _describe(records: Records, given: Json) -> Any:
    key = _table(records, given, "read")
    return describe(
        records.catalog.tables[key], records.access, records.situation.language, records.catalog
    )


async def _list(records: Records, given: Json) -> Any:
    key = _table(records, given, "read")
    limit = min(int(given.get("limit") or 20), MAX_LIMIT)
    page = await records.list(
        key,
        where=given.get("where"),
        sort=cast(list[str], given.get("sort") or []),
        limit=limit,
        after=given.get("after"),
    )
    found = await annotate(records, records.catalog.tables[key], page.records)
    return {"records": found, "next": page.next, "total": page.total}


async def _get(records: Records, given: Json) -> Any:
    key = _table(records, given, "read")
    (found,) = await annotate(
        records, records.catalog.tables[key], [await records.read(key, _id(given))]
    )
    return found


async def _create(records: Records, given: Json) -> Any:
    key = _table(records, given, "create")
    made = await records.create(key, cast(Json, given.get("values") or {}))
    return records.to_json(records.catalog.tables[key], made)


async def _update(records: Records, given: Json) -> Any:
    key = _table(records, given, "write")
    changed = await records.update(
        key, _id(given), cast(Json, given.get("values") or {}), given.get("etag")
    )
    return records.to_json(records.catalog.tables[key], changed)


async def _delete(records: Records, given: Json) -> Any:
    key = _table(records, given, "delete")
    gone = await records.delete(key, _id(given), given.get("etag"))
    return records.to_json(records.catalog.tables[key], gone)


async def _action(records: Records, given: Json) -> Any:
    key = _table(records, given, "read")
    return await run(
        records,
        key,
        str(given.get("action")),
        _id(given),
        given=cast(Json, given.get("inputs") or {}),
        etag=given.get("etag"),
    )


async def _aggregate(records: Records, given: Json) -> Any:
    key = _table(records, given, "read")
    found = await aggregate(
        records,
        key,
        where=given.get("where"),
        measure=str(given.get("measure") or "count"),
        field=given.get("field"),
        group=given.get("group"),
        period=given.get("period"),
        limit=min(int(given.get("limit") or 10), MAX_GROUPS),
    )
    return {"total": found.total, "groups": found.groups, "other": found.other}


async def _search(records: Records, given: Json) -> Any:
    return await search(records, str(given.get("query", "")), scope=given.get("scope"), per_list=5)


_WHERE = {
    "description": (
        'A condition in the filter language, e.g. {"field": "state", "op": "eq", "value": "open"};'
        " groups with and, or, not."
    ),
    "type": "object",
}
_ID = {"type": "string", "description": "The record's ID (sys_id)."}
_ETAG = {"type": "string", "description": "The record's version (_etag), as last read."}

TOOLS = (
    Tool(
        "list_tables",
        "List tables",
        "The tables you may read, and what you may do with each.",
        None,
        {},
        (),
        _list_tables,
    ),
    Tool(
        "describe_table",
        "Describe a table",
        "A table's fields (types, choices, which you may set), its display field and the tables"
        " pointing to it.",
        "read",
        {},
        ("table",),
        _describe,
    ),
    Tool(
        "list_records",
        "List records",
        "A page of a table's records, filtered and sorted; `next` continues it.",
        "read",
        {
            "where": _WHERE,
            "sort": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields, `-` for descending.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            "after": {"type": "string"},
        },
        ("table",),
        _list,
    ),
    Tool(
        "get_record",
        "Read a record",
        "One record, with display values and what you may do with it.",
        "read",
        {"id": _ID},
        ("table", "id"),
        _get,
    ),
    Tool(
        "create_record",
        "Create a record",
        "A new record from field values.",
        "create",
        {"values": {"type": "object"}},
        ("table", "values"),
        _create,
        read_only=False,
    ),
    Tool(
        "update_record",
        "Change a record",
        "Changes fields; needs the version you read, so nobody's change is lost.",
        "write",
        {"id": _ID, "values": {"type": "object"}, "etag": _ETAG},
        ("table", "id", "values", "etag"),
        _update,
        read_only=False,
    ),
    Tool(
        "delete_record",
        "Delete a record",
        "Deletes a record (it can be restored); needs the version you read.",
        "delete",
        {"id": _ID, "etag": _ETAG},
        ("table", "id", "etag"),
        _delete,
        read_only=False,
        destructive=True,
    ),
    Tool(
        "run_action",
        "Run an action",
        "Runs one of a record's actions (listed in its `_actions`); checked as for people.",
        "read",
        {"id": _ID, "action": {"type": "string"}, "inputs": {"type": "object"}, "etag": _ETAG},
        ("table", "id", "action"),
        _action,
        read_only=False,
    ),
    Tool(
        "aggregate",
        "Totals",
        "A count, sum, average, smallest or largest value, overall or per group.",
        "read",
        {
            "where": _WHERE,
            "measure": {"type": "string", "enum": sorted(MEASURES)},
            "field": {"type": "string"},
            "group": {"type": "string"},
            "period": {"type": "string", "enum": sorted(PERIODS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_GROUPS},
        },
        ("table",),
        _aggregate,
    ),
    Tool(
        "search",
        "Search",
        "Searches every list you may read at once (2 to 100 characters).",
        None,
        {
            "query": {"type": "string", "minLength": 2, "maxLength": 100},
            "scope": {"type": "string", "description": "One list view's name."},
        },
        ("query",),
        _search,
    ),
)


def offered(records: Records) -> list[Json]:
    """The tools for this token: each table-bound tool lists only the tables it may use."""
    found: list[Json] = []
    for tool in TOOLS:
        properties = dict(tool.properties)
        if tool.operation is not None:
            tables = sorted(
                key
                for key, t in records.catalog.tables.items()
                if not t.retired and not t.switched_off and records.access.of(key, tool.operation)
            )
            if not tables:
                continue  # nothing it could do
            properties = {"table": {"type": "string", "enum": tables}} | properties
        found.append(
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(tool.required),
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": tool.read_only,
                    "destructiveHint": tool.destructive,
                    "openWorldHint": False,
                },
            }
        )
    return found


def tool_named(name: str) -> Tool | None:
    return next((t for t in TOOLS if t.name == name), None)
