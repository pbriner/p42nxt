"""Translations of what modules define (table and field names, menus, actions, widgets, roles …).

Those texts live in the records as `{"en": …, "de": …}`, written from each module's manifest on
every install and upgrade, in English. Translations are kept apart, in `sys.translation`, and
applied onto the records after each of those writes: the installation's own over the module's,
and only while the English they were made from is still the English (else they're outdated, and
English shows). Export and import exchange them as JSON, all or nothing.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.sql import ident, qualified
from p42cloud.data.types.translated_text import LANGUAGE as CODE
from p42cloud.kernel.errors import FieldError, Problem

ENGLISH = "en"
MAX_TEXT = 2000
MAX_TEXTS = 20_000
SEPARATOR = "|"
_PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
_OWN = ("sys.language", "sys.translation")


@dataclass(frozen=True)
class _Where:
    """How one table's records are found by a natural ID, and whose module they are."""

    table: TableDef
    fields: tuple[str, ...]
    natural: str  # SQL over `r` (and `t` for fields)
    module: str  # SQL, may be NULL
    join: str = ""


@dataclass(frozen=True)
class Source:
    key: str
    module: str | None
    english: str


def _places(catalog: Catalog) -> list[_Where]:
    """Every table of definitions (the core's `sys` schema: what modules declare lands there)
    whose records hold translated texts and have a natural ID. Modules' own tables hold content,
    which isn't translated here."""
    found: list[_Where] = []
    for key, table in sorted(catalog.tables.items()):
        if table.retired or key in _OWN or table.schema != "sys":
            continue
        fields = tuple(
            f.name for f in table.fields if f.field.type == "translated_text" and not f.retired
        )
        if not fields:
            continue
        names = {f.name: f for f in table.fields if not f.retired}
        if key == "sys.table":
            found.append(_Where(table, fields, "r.schema || '.' || r.name", "r.schema"))
        elif key == "sys.field":
            found.append(
                _Where(
                    table,
                    fields,
                    "t.schema || '.' || t.name || '.' || r.name",
                    "t.schema",
                    'JOIN sys."table" t ON t.sys_id = r."table"',
                )
            )
        elif "name" in names and names["name"].field.unique:
            module = "r.module" if "module" in names else "NULL"
            found.append(_Where(table, fields, "r.name", module))
    return found


async def sources(
    conn: AsyncConnection, catalog: Catalog, module: str | None = None
) -> list[Source]:
    """Every translatable text there is now, with its key and English, sorted by key."""
    found: list[Source] = []
    for place in _places(catalog):
        table = qualified(place.table.schema, place.table.name)
        for name in place.fields:
            rows = await conn.execute(
                text(
                    f"SELECT {place.natural} AS natural, {place.module} AS module,"  # noqa: S608
                    f" r.{ident(name)} ->> 'en' AS english FROM {table} r {place.join}"
                    f" WHERE r.deleted IS NULL AND r.{ident(name)} ->> 'en' IS NOT NULL"
                    + (f" AND {place.module} = :m" if module else "")
                ),
                {"m": module},
            )
            found.extend(
                Source(_key(place.table, row.natural, name), row.module, row.english)
                for row in rows
            )
    return sorted(found, key=lambda s: s.key)


def _key(table: TableDef, natural: str, field: str) -> str:
    return SEPARATOR.join((f"{table.schema}.{table.name}", natural, field))


async def _kept(conn: AsyncConnection, language: str) -> dict[str, dict[str, Any]]:
    """The translation in force per key: the installation's over the module's."""
    rows = await conn.execute(
        text(
            "SELECT DISTINCT ON (key) key, text, english, origin FROM sys.translation"
            " WHERE language = :l AND deleted IS NULL"
            " ORDER BY key, origin = 'installation' DESC"
        ),
        {"l": language},
    )
    return {r.key: dict(r._mapping) for r in rows}  # pyright: ignore[reportPrivateUsage]


async def export(
    conn: AsyncConnection, catalog: Catalog, language: str, module: str | None = None
) -> dict[str, Any]:
    """Every text in English with its translation and state: missing, done or outdated."""
    kept = await _kept(conn, language)
    texts: list[dict[str, Any]] = []
    for source in await sources(conn, catalog, module):
        have = kept.get(source.key)
        state = (
            "missing"
            if have is None
            else ("done" if have["english"] == source.english else "outdated")
        )
        texts.append(
            {
                "key": source.key,
                "english": source.english,
                "text": have["text"] if have else "",
                "state": state,
            }
        )
    return {"language": language, "module": module, "texts": texts}


def check_language(code: str) -> str:
    if not CODE.match(code) or code == ENGLISH:
        raise Problem(
            422, "validation", "A language code like de or fr-CH, not English (the source)."
        )
    return code


def _items(given: Any) -> list[dict[str, Any]]:
    """The entries of a file's `texts`; anything that isn't an object counts as an empty one
    (and is refused as an unknown text)."""
    found: list[Any] = given if isinstance(given, list) else []  # pyright: ignore[reportUnknownVariableType]
    return [cast(dict[str, Any], i) if isinstance(i, dict) else {} for i in found]


def _placeholders(value: str) -> list[str]:
    return sorted(_PLACEHOLDER.findall(value))


def _checked(given: Any, known: dict[str, Source], language: str) -> list[tuple[Source, str]]:
    """The texts of an import file, all checked first: nothing is changed if one is wrong."""
    if not isinstance(given, dict) or not isinstance(given.get("texts"), list):  # pyright: ignore[reportUnknownMemberType]
        raise Problem(422, "validation", 'Expected {"language": …, "texts": [{"key", "text"}]}.')
    body: dict[str, Any] = given  # pyright: ignore[reportUnknownVariableType]
    if body.get("language") not in (None, language):
        raise Problem(422, "validation", f"The file is for {body.get('language')}, not {language}.")
    items = _items(body["texts"])
    if len(items) > MAX_TEXTS:
        raise Problem(422, "too_many", f"At most {MAX_TEXTS} texts at once.")
    errors: list[FieldError] = []
    checked: list[tuple[Source, str]] = []
    for item in items:
        key = item.get("key")
        value = item.get("text", "")
        source = known.get(key) if isinstance(key, str) else None
        if source is None:
            errors.append(FieldError(str(key)[:300], "unknown", "No such text."))
        elif not isinstance(value, str) or len(value.strip()) > MAX_TEXT:
            errors.append(FieldError(source.key, "invalid", f"Text up to {MAX_TEXT} characters."))
        elif value.strip() and _placeholders(value) != _placeholders(source.english):
            errors.append(
                FieldError(source.key, "placeholders", "Keep the {…} parts of the English.")
            )
        else:
            checked.append((source, value.strip()))
    if errors:
        raise Problem(422, "validation", "Some texts can't be imported.", errors=errors[:100])
    return checked


async def import_texts(
    conn: AsyncConnection, catalog: Catalog, language: str, given: Any
) -> dict[str, int]:
    """The installation's translations from an export file; an empty text removes one."""
    known = {s.key: s for s in await sources(conn, catalog)}
    counts = {"added": 0, "changed": 0, "removed": 0, "unchanged": 0}
    for source, value in _checked(given, known, language):
        current = (
            await conn.execute(
                text(
                    "SELECT sys_id, text, english FROM sys.translation WHERE language = :l"
                    " AND key = :k AND origin = 'installation' AND deleted IS NULL"
                ),
                {"l": language, "k": source.key},
            )
        ).first()
        if not value:
            if current is not None:
                await conn.execute(
                    text("UPDATE sys.translation SET deleted = now() WHERE sys_id = :i"),
                    {"i": current.sys_id},
                )
            counts["removed" if current is not None else "unchanged"] += 1
            continue
        if current is None:
            await conn.execute(
                text(
                    "INSERT INTO sys.translation (language, key, text, english, origin, module)"
                    " VALUES (:l, :k, :t, :e, 'installation', :m)"
                ),
                {
                    "l": language,
                    "k": source.key,
                    "t": value,
                    "e": source.english,
                    "m": source.module,
                },
            )
            counts["added"] += 1
        elif (current.text, current.english) != (value, source.english):
            await conn.execute(
                text("UPDATE sys.translation SET text = :t, english = :e WHERE sys_id = :i"),
                {"t": value, "e": source.english, "i": current.sys_id},
            )
            counts["changed"] += 1
        else:
            counts["unchanged"] += 1
    return counts


async def ship(
    conn: AsyncConnection, catalog: Catalog, module: str, language: str, given: Any
) -> int:
    """A module's own translations (its `translations/<language>.json`), replacing those it
    shipped before. Texts it no longer has are left out, not refused."""
    known = {s.key: s for s in await sources(conn, catalog, module)}
    body: dict[str, Any] = given if isinstance(given, dict) else {}  # pyright: ignore[reportUnknownVariableType]
    items = [i for i in _items(body.get("texts") or []) if i.get("key") in known]
    checked = _checked({"texts": items}, known, language)
    await conn.execute(
        text(
            "UPDATE sys.translation SET deleted = now() WHERE origin = 'module'"
            " AND module = :m AND language = :l AND deleted IS NULL"
        ),
        {"m": module, "l": language},
    )
    for source, value in checked:
        if value:
            await conn.execute(
                text(
                    "INSERT INTO sys.translation (language, key, text, english, origin, module)"
                    " VALUES (:l, :k, :t, :e, 'module', :m)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"l": language, "k": source.key, "t": value, "e": source.english, "m": module},
            )
    return len(checked)


async def apply(conn: AsyncConnection, catalog: Catalog) -> int:
    """Puts the translations in force onto the records, after the modules wrote their English.
    A language with no translation in force (removed, outdated) is taken off a text it has a
    record for, so it falls back to English."""
    languages = [
        str(code)
        for code in (
            await conn.execute(
                text("SELECT DISTINCT language FROM sys.translation WHERE language <> 'en'")
            )
        ).scalars()
    ]
    if not languages:
        return 0
    kept = {lang: await _kept(conn, lang) for lang in languages}
    touched = await conn.execute(
        text("SELECT DISTINCT key FROM sys.translation WHERE language <> 'en'")
    )
    managed = set(touched.scalars())
    written = 0
    for place in _places(catalog):
        table = qualified(place.table.schema, place.table.name)
        for name in place.fields:
            rows = await conn.execute(
                text(
                    f"SELECT r.sys_id, {place.natural} AS natural,"  # noqa: S608
                    f" r.{ident(name)} AS value FROM {table} r {place.join}"
                    f" WHERE r.deleted IS NULL AND r.{ident(name)} ->> 'en' IS NOT NULL"
                ),
            )
            for row in rows:
                key = _key(place.table, row.natural, name)
                if key not in managed:
                    continue
                value: dict[str, str] = dict(row.value)
                wanted = dict(value)
                for lang in languages:
                    have = kept[lang].get(key)
                    if have is not None and have["english"] == value.get(ENGLISH):
                        wanted[lang] = have["text"]
                    else:
                        wanted.pop(lang, None)
                if wanted != value:
                    await conn.execute(
                        text(
                            f"UPDATE {table} SET {ident(name)} = CAST(:v AS jsonb)"  # noqa: S608
                            " WHERE sys_id = :i"
                        ),
                        {"v": _json(wanted), "i": row.sys_id},
                    )
                    written += 1
    return written


def _json(value: dict[str, str]) -> str:
    return json.dumps(value, ensure_ascii=False)


STARTING = (("en", "English", True), ("de", "Deutsch", True))


async def seed(conn: AsyncConnection) -> None:
    """The languages every installation starts with; kept as administrators changed them."""
    for code, name, offered in STARTING:
        await conn.execute(
            text(
                "INSERT INTO sys.language (code, name, offered)"
                " SELECT CAST(:c AS varchar), CAST(:n AS varchar), CAST(:o AS boolean)"
                " WHERE NOT EXISTS (SELECT 1 FROM sys.language WHERE code = CAST(:c AS varchar))"
            ),
            {"c": code, "n": name, "o": offered},
        )
