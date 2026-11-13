"""Checking the audit log: its hash chain, and that the months ahead exist.

`verify_chain` recomputes every entry's hash from its fields and the previous hash, exactly as
the database computed it when sealing (engine_steps, p42_audit_seal). It finds an entry that was
changed, removed or inserted, and newest entries that were cut off.

Whoever owns the database can rewrite the whole chain, head included, and it verifies again. Only
an anchor finds that: an entry's number and hash noted outside the database earlier, which the
rewritten chain no longer has.
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
class Anchor:
    """An entry's number and hash, kept outside the database."""

    seq: int
    hash: bytes

    @classmethod
    def parse(cls, value: str) -> Anchor:
        """From `<seq>:<hash in hex>`, as `p42cloud audit verify` prints it."""
        seq, _, digest = value.partition(":")
        return cls(int(seq), bytes.fromhex(digest))

    def __str__(self) -> str:
        return f"{self.seq}:{self.hash.hex()}"


@dataclass(frozen=True)
class ChainCheck:
    entries: int
    head: Anchor
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


def _entry_problem(row: Any, expected: int, current: bytes, anchor: Anchor | None) -> str | None:
    if row.seq != expected:
        return f"Entry {expected} is missing."
    if current != bytes(row.chain_hash):
        return f"Entry {row.seq} ({row.sys_id}) was changed."
    if anchor is not None and row.seq == anchor.seq and current != anchor.hash:
        return f"Entry {row.seq} differs from the copy kept outside."
    return None


async def verify_chain(conn: AsyncConnection, anchor: Anchor | None = None) -> ChainCheck:
    top = (await conn.execute(_HEAD)).one()
    head = Anchor(top.seq, bytes(top.hash))
    unsealed = await conn.scalar(_UNSEALED)
    if unsealed:
        return ChainCheck(0, head, f"{unsealed} entries aren't sealed.")
    previous, expected, checked = bytes(top.pruned_hash), top.pruned_seq + 1, 0
    anchored = anchor is None or anchor == Anchor(top.pruned_seq, previous)
    while expected <= head.seq:
        rows = (
            await conn.execute(
                _ENTRIES, {"after": expected - 1, "up_to": head.seq, "limit": _BATCH}
            )
        ).all()
        if not rows:
            break
        for row in rows:
            current = entry_hash(previous, row)
            if problem := _entry_problem(row, expected, current, anchor):
                return ChainCheck(checked, head, problem)
            anchored = anchored or (anchor is not None and row.seq == anchor.seq)
            previous, expected, checked = current, expected + 1, checked + 1
    problem = None
    if expected <= head.seq:
        problem = f"Entries {expected} to {head.seq} are missing."
    elif previous != head.hash:
        problem = "The chain's head doesn't match its entries."
    elif not anchored and anchor is not None:
        problem = f"Entry {anchor.seq}, kept outside, isn't in the chain."
    return ChainCheck(checked, head, problem)


# Missing months are reported this far ahead: writes fail only once the current month is
# missing, so this gives a month or more to run `p42cloud upgrade`.
WARN_MONTHS_AHEAD = 2


async def missing_months(conn: AsyncConnection, *, today: datetime | None = None) -> list[str]:
    """Audit partitions missing from this month to WARN_MONTHS_AHEAD months ahead."""
    this = (today or datetime.now(UTC)).date().replace(day=1)
    present = set((await conn.execute(_PARTITIONS)).scalars())
    months = (add_months(this, n) for n in range(WARN_MONTHS_AHEAD + 1))
    return [m.strftime("%Y-%m") for m in months if f"audit_log_{m:%Y_%m}" not in present]
