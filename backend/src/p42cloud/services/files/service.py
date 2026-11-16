"""Attaching files to records, listing, reading and removing them.

The content is checked by what it is (its first bytes), not by its name or the type the browser
says: only documents, images and plain text, never anything a browser would run (HTML, SVG). It's
sealed with the master key and kept apart from the record describing it; removing a file marks
that record, the content stays. Rights are the record's: whoever may read it reads its files,
whoever may change it attaches and removes them.
"""

import hashlib
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from p42cloud.data.records import Records
from p42cloud.data.settings import value as setting
from p42cloud.kernel.db import switch_role
from p42cloud.kernel.errors import Problem
from p42cloud.kernel.secrets import process_sealer

PURPOSE = "file"
MAX_NAME = 200
_UNSAFE_NAME = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")
_OFFICE = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_TEXT = {".txt": "text/plain", ".csv": "text/csv", ".md": "text/markdown"}
# What a browser may show itself rather than download.
SHOWN_INLINE = frozenset({"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"})


@dataclass(frozen=True)
class Stored:
    """A file's record, as the API gives it."""

    sys_id: str
    name: str
    type: str
    size: int
    created: str
    created_by: str


def clean_name(given: str) -> str:
    """The file's own name, without folders or characters file systems refuse."""
    base = given.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_NAME.sub("_", base).strip(" .")
    if not cleaned:
        raise Problem(422, "validation", "The file needs a name.")
    if len(cleaned) > MAX_NAME:
        stem, dot, ending = cleaned.rpartition(".")
        cleaned = (stem[: MAX_NAME - len(ending) - 1] + dot + ending) if dot else cleaned[:MAX_NAME]
    return cleaned


def _office(data: bytes, ending: str) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as packed:
            found = "[Content_Types].xml" in packed.namelist()
    except zipfile.BadZipFile:
        return None
    return _OFFICE[ending] if found else None


def _plain_text(data: bytes, ending: str) -> str | None:
    if b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return _TEXT[ending]


_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def kind_of(data: bytes, name: str) -> str | None:
    """What the file is, from its first bytes (the name only tells Office files and text
    apart); None for anything not accepted."""
    ending = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    found = next((kind for start, kind in _SIGNATURES if data.startswith(start)), None)
    if found is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        found = "image/webp"
    if found is None and data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1"):
        found = "image/heic"
    if found is None and data.startswith(b"PK\x03\x04") and ending in _OFFICE:
        found = _office(data, ending)
    elif found is None and ending in _TEXT:
        found = _plain_text(data, ending)
    return found


async def limit(records: Records) -> int:
    """The largest file accepted, in bytes (the setting Largest file, in MB)."""
    await switch_role(records.conn, "sys")
    return int(await setting(records.conn, "sys.file_max_mb") or 20) * 1024 * 1024


async def _may_change(records: Records, table: str, record_id: uuid.UUID) -> None:
    found = records.catalog.table(table)
    if found is None or not found.files:
        raise Problem(404, "no_files", "Files can't be attached to these records.", detail=table)
    await records.read(table, record_id)  # 404 if they may not read it
    allowed = await records.may(found, [record_id], ("write",))
    if not allowed.get(record_id, {}).get("write"):
        raise Problem(403, "forbidden", "You may not change this record, so not its files.")


async def attach(
    records: Records, table: str, record_id: uuid.UUID, name: str, data: bytes
) -> Stored:
    """Attaches a file to a record the person may change; in their transaction."""
    await _may_change(records, table, record_id)
    clean = clean_name(name)
    if not data:
        raise Problem(422, "validation", "The file is empty.")
    largest = await limit(records)
    if len(data) > largest:
        raise Problem(413, "too_large", f"Files are at most {largest // (1024 * 1024)} MB.")
    kind = kind_of(data, clean)
    if kind is None:
        raise Problem(
            415,
            "not_accepted",
            "This kind of file isn't accepted: PDF, images, Office documents and plain text are.",
        )
    await switch_role(records.conn, "sys")
    content = await records.conn.scalar(
        text("INSERT INTO sys.file_data (sealed) VALUES (:sealed) RETURNING id"),
        {"sealed": process_sealer().seal(data, PURPOSE)},
    )
    row: Any = (
        await records.conn.execute(
            text(
                "INSERT INTO sys.file (name, type, size, sha256, record_table, record_id, about,"
                " content) VALUES (:name, :type, :size, :sha, :table, :record, :about, :content)"
                " RETURNING sys_id, name, type, size, created, created_by"
            ),
            {
                "name": clean,
                "type": kind,
                "size": len(data),
                "sha": hashlib.sha256(data).hexdigest(),
                "table": table,
                "record": str(record_id),
                "about": record_id,
                "content": str(content),
            },
        )
    ).one()
    return _stored(row)


def _stored(row: Any) -> Stored:
    return Stored(
        str(row.sys_id),
        row.name,
        row.type,
        row.size,
        row.created.isoformat(),
        str(row.created_by),
    )


async def listed(records: Records, table: str, record_id: uuid.UUID) -> list[Stored]:
    """The files of a record the person may read, newest first."""
    found = records.catalog.table(table)
    if found is None or not found.files:
        raise Problem(404, "no_files", "Files can't be attached to these records.", detail=table)
    await records.read(table, record_id)
    await switch_role(records.conn, "sys")
    rows = await records.conn.execute(
        text(
            "SELECT sys_id, name, type, size, created, created_by FROM sys.file"
            " WHERE record_table = :table AND record_id = :record AND deleted IS NULL"
            " ORDER BY created DESC"
        ),
        {"table": table, "record": str(record_id)},
    )
    return [_stored(r) for r in rows]


async def _file(records: Records, file_id: uuid.UUID) -> Any:
    await switch_role(records.conn, "sys")
    row = (
        await records.conn.execute(
            text(
                "SELECT sys_id, name, type, size, created, created_by, record_table, record_id,"
                " content FROM sys.file WHERE sys_id = :id AND deleted IS NULL"
            ),
            {"id": file_id},
        )
    ).first()
    if row is None:
        raise Problem(404, "not_found", "There's no such file.")
    return row


async def read(records: Records, file_id: uuid.UUID) -> tuple[Stored, bytes]:
    """A file and its content, for one who may read its record."""
    row = await _file(records, file_id)
    try:
        await records.read(row.record_table, uuid.UUID(row.record_id))
    except Problem as refused:  # not theirs to read: as if there were no such file
        raise Problem(404, "not_found", "There's no such file.") from refused
    await switch_role(records.conn, "sys")
    sealed = await records.conn.scalar(
        text("SELECT sealed FROM sys.file_data WHERE id = CAST(:id AS uuid)"), {"id": row.content}
    )
    if sealed is None:
        raise Problem(404, "not_found", "The file's content is missing.")
    return _stored(row), process_sealer().unseal(str(sealed), PURPOSE)


async def remove(records: Records, file_id: uuid.UUID) -> None:
    """Removes a file from its record (it's marked; the content stays), for one who may change
    the record."""
    row = await _file(records, file_id)
    try:
        await _may_change(records, row.record_table, uuid.UUID(row.record_id))
    except Problem as refused:
        if refused.status == 404:  # noqa: PLR2004
            raise Problem(404, "not_found", "There's no such file.") from refused
        raise
    await switch_role(records.conn, "sys")
    await records.conn.execute(
        text("UPDATE sys.file SET deleted = now() WHERE sys_id = :id"), {"id": file_id}
    )
