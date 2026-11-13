"""Checking the audit log: its hash chain, and that the months ahead exist.

`verify_chain` recomputes every entry's hash from its fields and the previous hash, exactly as
the database computed it when sealing (engine_steps, p42_audit_seal). It finds an entry that was
changed, removed or inserted, and newest entries that were cut off. Comparing the head with a
copy kept outside the database also finds a chain rewritten as a whole.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.upgrade import add_months

_HEAD = text("SELECT seq, hash, pruned_seq, pruned_hash FROM sys.audit_chain")
_UNSEALED = text("SELECT count(*) FROM sys.audit_log WHERE seq IS NULL")
_ENTRIES = text(
    "SELECT seq, sys_id, (extract(epoch FROM created) * 1000000)::bigint AS created_us,"
    " table_name, record_id, operation, person_id, on_behalf_of, channel, request_id,"
    " changes::text AS changes, chain_hash"
    " FROM sys.audit_log WHERE seq > :after AND seq <= :up_to ORDER BY seq LIMIT :limit"
)
_PARTITIONS = text(
    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid"
    " WHERE i.inhparent = 'sys.audit_log'::regclass"
)
_BATCH = 1000


@dataclass(frozen=True)
class ChainCheck:
    entries: int
    head_seq: int
    problem: str | None

    @property
    def intact(self) -> bool:
        return self.problem is None


def _part(value: object) -> str:
    if value is None:
        return "-"
    rendered = str(value)
    return f"{len(rendered.encode())}:{rendered}"


def entry_hash(previous: bytes, row: Any) -> bytes:
    canonical = "".join(
        _part(v)
        for v in (
            row.seq,
            row.sys_id,
            row.created_us,
            row.table_name,
            row.record_id,
            row.operation,
            row.person_id,
            row.on_behalf_of,
            row.channel,
            row.request_id,
            row.changes,
        )
    )
    return hashlib.sha256(previous + canonical.encode()).digest()


async def verify_chain(conn: AsyncConnection) -> ChainCheck:
    head = (await conn.execute(_HEAD)).one()
    unsealed = await conn.scalar(_UNSEALED)
    if unsealed:
        return ChainCheck(0, head.seq, f"{unsealed} entries aren't sealed.")
    previous, expected, checked = bytes(head.pruned_hash), head.pruned_seq + 1, 0
    while expected <= head.seq:
        rows = (
            await conn.execute(
                _ENTRIES, {"after": expected - 1, "up_to": head.seq, "limit": _BATCH}
            )
        ).all()
        if not rows:
            break
        for row in rows:
            if row.seq != expected:
                return ChainCheck(checked, head.seq, f"Entry {expected} is missing.")
            current = entry_hash(previous, row)
            if current != bytes(row.chain_hash):
                return ChainCheck(checked, head.seq, f"Entry {row.seq} ({row.sys_id}) was changed.")
            previous, expected, checked = current, expected + 1, checked + 1
    if expected <= head.seq:
        return ChainCheck(checked, head.seq, f"Entries {expected} to {head.seq} are missing.")
    if previous != bytes(head.hash):
        return ChainCheck(checked, head.seq, "The chain's head doesn't match its entries.")
    return ChainCheck(checked, head.seq, None)


async def missing_months(conn: AsyncConnection, *, today: datetime | None = None) -> list[str]:
    """This month's and next month's audit partitions, if missing (writes would fail)."""
    this = (today or datetime.now(UTC)).date().replace(day=1)
    following = add_months(this, 1)
    present = set((await conn.execute(_PARTITIONS)).scalars())
    return [
        month.strftime("%Y-%m")
        for month in (this, following)
        if f"audit_log_{month:%Y_%m}" not in present
    ]
