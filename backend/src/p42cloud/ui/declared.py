"""Writing what a module declares for the screens into records, as roles and settings are.

Each declared record is found by its name: new ones are made, existing ones brought to the
declaration, and those the module no longer declares are retired. Records admins made themselves
(`custom`) are never touched.
"""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.sql import ident


def _placeholder(name: str, json_fields: frozenset[str]) -> str:
    return f"CAST(:{name} AS jsonb)" if name in json_fields else f":{name}"


async def sync(
    conn: AsyncConnection,
    table: str,
    module: str,
    rows: list[dict[str, Any]],
    json_fields: frozenset[str],
) -> None:
    """Brings `table`'s records of `module` to `rows` (each with a unique `name`), as the owner."""
    for row in rows:
        values = {
            k: json.dumps(v) if k in json_fields and v is not None else v for k, v in row.items()
        } | {"module": module}
        columns = [c for c in values if c != "name"]
        changes = ", ".join(f"{ident(c)} = {_placeholder(c, json_fields)}" for c in columns)
        changed = await conn.execute(
            text(
                f"UPDATE {table} SET {changes}, custom = false, deleted = NULL"  # noqa: S608
                " WHERE name = :name"
            ),
            values,
        )
        if changed.rowcount == 0:
            made = values | {"custom": False}
            names = ", ".join(ident(c) for c in made)
            placeholders = ", ".join(_placeholder(c, json_fields) for c in made)
            await conn.execute(
                text(f"INSERT INTO {table} ({names}) VALUES ({placeholders})"),  # noqa: S608
                made,
            )
    await conn.execute(
        text(
            f"UPDATE {table} SET deleted = now() WHERE module = :module"  # noqa: S608
            " AND NOT custom AND deleted IS NULL AND NOT name = ANY(:names)"
        ),
        {"module": module, "names": [r["name"] for r in rows]},
    )


def label_of(value: str | dict[str, str] | None) -> dict[str, str] | None:
    """A declared label as a translated text."""
    return {"en": value} if isinstance(value, str) else value
