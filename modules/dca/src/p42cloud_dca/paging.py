"""Every record of a list, page by page: a plan's orders grow past one page within months."""

from typing import Any

from p42cloud.sdk import Client

PAGE = 500  # the most the client gives at once


async def every(
    client: Client, table: str, *, where: Any = None, sort: list[str] | None = None
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        page = await client.list(table, where=where, sort=sort, limit=PAGE, after=after)
        found += page["records"]
        after = page["next"]
        if not after:
            return found
