"""What a screen needs besides the values: a table described for a person, and records with the
display values of their references and choices and what the person may do with each.

The UI builds every list and form from these (it knows no table of its own).
"""

from typing import Any
from uuid import UUID

from p42cloud.data.catalog import Catalog, FieldDef, TableDef
from p42cloud.data.conditions import SYSTEM_FIELDS
from p42cloud.data.records import Record, Records
from p42cloud.kernel.context import Access
from p42cloud.kernel.errors import Problem

OPERATIONS = ("read", "create", "write", "delete", "restore")
_PER_RECORD = ("write", "delete", "restore")


def translated(value: Any, language: str) -> Any:
    """A translated text in the language, falling back to English."""
    if isinstance(value, dict):
        texts: dict[str, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return texts.get(language, texts.get("en"))
    return value


def _covers(access: Access, table: TableDef, operation: str, name: str) -> bool:
    """Whether some grant lets the person do this to the field on some records."""
    return any(
        p.fields is None or name in SYSTEM_FIELDS or name in p.fields
        for p in access.of(table.key, operation)
    )


def _field(table: TableDef, f: FieldDef, access: Access, language: str) -> dict[str, Any]:
    system = f.name in SYSTEM_FIELDS
    described: dict[str, Any] = {
        "name": f.name,
        "type": f.field.type,
        "label": translated(f.field.label, language),
        "help": translated(f.field.help, language),
        "required": f.field.required,
        "unique": f.field.unique,
        "default": f.type.to_json(f.field.default) if f.field.default is not None else None,
        "system": system,
        "read": _covers(access, table, "read", f.name),
        "create": not system and _covers(access, table, "create", f.name),
        "write": not system and _covers(access, table, "write", f.name),
        "filter": f.type.kind is not None,
        # Which operators apply (the filter language's kinds); None: not filtered or sorted.
        "kind": f.type.kind,
    }
    options = dict(f.field.options)
    if f.field.type == "choice":
        options["values"] = [
            {
                "value": v["value"],
                "label": translated(v.get("label"), language),
                "tone": v.get("tone"),
            }
            for v in options.get("values", [])
        ]
    described["options"] = options
    return described


def _referenced_by(
    catalog: Catalog, table: TableDef, access: Access, language: str
) -> list[dict[str, Any]]:
    """The tables pointing to this one, through fields the person may read: related lists."""
    found: list[dict[str, Any]] = []
    for other in catalog.tables.values():
        if other.retired or other.switched_off or not access.of(other.key, "read"):
            continue
        for f in other.active_fields():
            if f.field.type != "reference" or f.field.options.get("table") != table.key:
                continue
            if not _covers(access, other, "read", f.name):
                continue
            found.append(
                {
                    "table": other.key,
                    "field": f.name,
                    "label": translated(other.label_plural or other.label, language),
                    "field_label": translated(f.field.label, language),
                }
            )
    return found


def describe(
    table: TableDef, access: Access, language: str, catalog: Catalog | None = None
) -> dict[str, Any]:
    """The table as one person sees it: labels in their language, what they may do, and (with
    the catalog) which tables point to it."""
    if not access.of(table.key, "read"):
        raise Problem(403, "forbidden", "You may not do this.", detail=table.key)
    system = [FieldDef(n, f, -1) for n, f in SYSTEM_FIELDS.items()]
    return {
        "table": table.key,
        "label": translated(table.label, language),
        "label_plural": translated(table.label_plural, language),
        "display": table.display,
        "personal": table.owner_field is not None,
        "operations": {op: bool(access.of(table.key, op)) for op in OPERATIONS},
        "fields": [_field(table, f, access, language) for f in [*table.active_fields(), *system]],
        "referenced_by": _referenced_by(catalog, table, access, language) if catalog else [],
    }


async def _displays(
    records: Records, catalog: Catalog, target_key: str, ids: set[UUID]
) -> dict[UUID, Any]:
    """The display values of referenced records the person may read; the others stay unnamed."""
    target = catalog.table(target_key)
    if target is None or not ids:
        return {}
    shown = target.display
    if shown is None:
        return {}
    try:
        page = await records.list(
            target_key,
            where={"field": "sys_id", "op": "in", "value": [str(i) for i in ids]},
            limit=len(ids),
            count=False,
        )
    except Problem:  # a table they may not read: no names
        return {}
    language = records.situation.language
    return {r.sys_id: translated(r.values.get(shown), language) for r in page.records}


async def annotate(records: Records, table: TableDef, found: list[Record]) -> list[dict[str, Any]]:
    """The records as JSON, each with `_display` (references and choices named) and `_access`
    (what the person may do with it)."""
    bodies = [records.to_json(table, r) for r in found]
    language = records.situation.language
    references: dict[str, set[UUID]] = {}
    for f in table.active_fields():
        target = f.type.references(f.field.options)
        if target is not None:
            references.setdefault(target, set()).update(
                r.values[f.name] for r in found if r.values.get(f.name) is not None
            )
    names = {
        key: await _displays(records, records.catalog, key, ids) for key, ids in references.items()
    }
    allowed = await records.may(table, [r.sys_id for r in found], _PER_RECORD)
    for record, body in zip(found, bodies, strict=True):
        display: dict[str, Any] = {}
        for f in table.active_fields():
            value = record.values.get(f.name)
            if value is None:
                continue
            target = f.type.references(f.field.options)
            if target is not None and value in names.get(target, {}):
                display[f.name] = names[target][value]
            elif f.field.type == "choice":
                labels = {v["value"]: v.get("label") for v in f.field.options.get("values", [])}
                display[f.name] = translated(labels.get(value), language)
        body["_display"] = display
        body["_access"] = allowed.get(record.sys_id, dict.fromkeys(_PER_RECORD, False))
    return bodies
