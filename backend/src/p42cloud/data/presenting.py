"""What a screen needs besides the values: a table described for a person, and records with the
display values of their references and choices and what the person may do with each.

The UI builds every list and form from these (it knows no table of its own).
"""

from typing import Any
from uuid import UUID

from p42cloud.data.catalog import Catalog, FieldDef, TableDef
from p42cloud.data.conditions import SYSTEM_FIELDS, Situation, default_of, is_dynamic
from p42cloud.data.records import Pointing, Record, Records
from p42cloud.kernel.context import Access
from p42cloud.kernel.errors import Problem

OPERATIONS = ("read", "create", "write", "delete", "restore")
MAX_NAMED = 500  # the records named at once per table (one page)
PATH_SEPARATOR = " \u203a "  # a single right angle quote between the names of a path
MAX_TREE_DEPTH = 30  # as deep as the database lets a hierarchy be
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


def _default(f: FieldDef, situation: Situation | None) -> Any:
    raw = f.field.default
    if raw is None:
        return None
    if is_dynamic(f, raw):
        # Worked out for the person asking (today, them), so a form can show it; the word too.
        return None if situation is None else f.type.to_json(default_of(f, situation))
    return f.type.to_json(raw)


def _field(
    table: TableDef, f: FieldDef, access: Access, language: str, situation: Situation | None
) -> dict[str, Any]:
    system = f.name in SYSTEM_FIELDS
    fixed = system or f.type.generated
    described: dict[str, Any] = {
        "name": f.name,
        "type": f.field.type,
        "label": translated(f.field.label, language),
        "help": translated(f.field.help, language),
        "required": f.field.required,
        "unique": f.field.unique,
        "default": _default(f, situation),
        # A default named by a word (`@me`, `@today+14d`): worked out on each create.
        "default_rule": f.field.default if is_dynamic(f, f.field.default) else None,
        "system": system,
        "read": _covers(access, table, "read", f.name),
        "create": not fixed and _covers(access, table, "create", f.name),
        "write": not fixed and _covers(access, table, "write", f.name),
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
        fields = {f.name: f for f in other.active_fields()}
        for f in other.active_fields():
            also = _also(table, f, fields)
            pointing = f.field.type == "reference" and f.field.options.get("table") == table.key
            if not (pointing or also) or not _covers(access, other, "read", f.name):
                continue
            entry: dict[str, Any] = {
                "table": other.key,
                "field": f.name,
                "label": translated(other.label_plural or other.label, language),
                "field_label": translated(f.field.label, language),
            }
            if also:
                entry["also"] = also
            found.append(entry)
    return found


def _also(table: TableDef, f: FieldDef, fields: dict[str, FieldDef]) -> dict[str, str] | None:
    """For a link to any record: the condition on its table field that makes it this table's."""
    named = f.field.options.get("table_field") if f.field.type == "any_record" else None
    holder = fields.get(str(named)) if named else None
    if holder is None:
        return None
    if holder.field.type == "reference":  # to sys.table: this table's record
        return {holder.name: str(table.id)} if table.id is not None else None
    return {holder.name: table.key}


def describe(
    table: TableDef,
    access: Access,
    language: str,
    catalog: Catalog | None = None,
    situation: Situation | None = None,
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
        # A hierarchy: the field naming each record's parent.
        "parent": table.parent_field,
        # Files may be attached to its records.
        "files": table.files,
        # Its records have a conversation.
        "comments": table.comments,
        # Its records have work notes (only for those who may change a record).
        "work_notes": table.work_notes,
        "operations": {op: bool(access.of(table.key, op)) for op in OPERATIONS},
        "fields": [
            _field(table, f, access, language, situation) for f in [*table.active_fields(), *system]
        ],
        "referenced_by": _referenced_by(catalog, table, access, language) if catalog else [],
    }


async def display_names(
    records: Records, catalog: Catalog, target_key: str, ids: set[UUID]
) -> dict[UUID, Any]:
    """The display values of referenced records the person may read; the others stay unnamed."""
    return {k: name for k, (name, _) in (await named(records, catalog, target_key, ids)).items()}


async def named(
    records: Records, catalog: Catalog, target_key: str, ids: set[UUID]
) -> dict[UUID, tuple[Any, bool]]:
    """The referenced records the person may read: each one's display value, and whether it's
    retired (deleted: history keeps pointing to it). Those they may not read aren't there."""
    target = catalog.table(target_key)
    if target is None or not ids or target.display is None:
        return {}
    rows = await _readable(records, target, ids)
    if target.parent_field is not None:
        return {
            i: (path, retired)
            for i, (path, retired) in (await paths(records, target, rows)).items()
        }
    language = records.situation.language
    return {
        i: (translated(r.values.get(target.display), language), retired)
        for i, (r, retired) in rows.items()
    }


async def _readable(
    records: Records, target: TableDef, ids: set[UUID]
) -> dict[UUID, tuple[Record, bool]]:
    """The records of these IDs the person may read, active or retired."""
    found: dict[UUID, tuple[Record, bool]] = {}
    for retired in (False, True):
        wanted = [str(i) for i in ids if i not in found]
        if not wanted:
            break
        try:
            page = await records.list(
                target.key,
                where={"field": "sys_id", "op": "in", "value": wanted},
                limit=min(len(wanted), MAX_NAMED),
                count=False,
                deleted=retired,
            )
        except Problem:  # a table they may not read: nothing
            return found
        for r in page.records:
            found[r.sys_id] = (r, retired)
    return found


async def paths(
    records: Records, tree: TableDef, rows: dict[UUID, tuple[Record, bool]]
) -> dict[UUID, tuple[str, bool]]:
    """Each record of a hierarchy named by its path from the top (the house, then its heating),
    through the records above it the person may read: one they may not read ends the path
    there, unnamed."""
    shown, language = tree.display, records.situation.language
    parent = str(tree.parent_field)
    known: dict[UUID, Record] = {i: r for i, (r, _) in rows.items()}
    for _ in range(MAX_TREE_DEPTH):
        missing = {
            p
            for r in known.values()
            if isinstance(p := r.values.get(parent), UUID) and p not in known
        }
        if not missing:
            break
        found = await _readable(records, tree, missing)
        if not found:
            break
        known |= {i: r for i, (r, _) in found.items()}
    named: dict[UUID, tuple[str, bool]] = {}
    for record_id, (_, retired) in rows.items():
        names: list[str] = []
        at: UUID | None = record_id
        while at is not None and at in known and len(names) < MAX_TREE_DEPTH:
            names.append(str(translated(known[at].values.get(shown or ""), language) or ""))
            up = known[at].values.get(parent)
            at = up if isinstance(up, UUID) else None
        named[record_id] = (PATH_SEPARATOR.join(reversed(names)), retired)
    return named


def target_of(catalog: Catalog, table: TableDef, f: FieldDef, record: Record) -> str | None:
    """The table a reference of this record points to: its declared one, or, for a link to any
    record, the one its table field names (a table's name, or its record of sys.table)."""
    declared = f.type.references(f.field.options)
    if declared is not None or f.field.type != "any_record":
        return declared
    holder = f.field.options.get("table_field")
    named_by = record.values.get(str(holder)) if holder else None
    if named_by is None:
        return None
    if isinstance(named_by, str) and "." in named_by:
        return named_by
    return next((t.key for t in catalog.tables.values() if t.id == named_by), None)


def _names_of(
    catalog: Catalog,
    table: TableDef,
    record: Record,
    names: dict[str, dict[UUID, tuple[Any, bool]]],
    language: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """A record's references and choices named, and the references' states where they aren't
    simply named: retired (named, marked), or hidden (the person may not read it: neither its
    name nor its ID is shown)."""
    display: dict[str, Any] = {}
    state: dict[str, str] = {}
    for f in table.active_fields():
        value = record.values.get(f.name)
        if value is None:
            continue
        target = target_of(catalog, table, f, record)
        if target is not None:
            known = names.get(target, {})
            if value not in known:
                state[f.name] = "hidden"
                continue
            display[f.name], retired = known[value]
            if retired:
                state[f.name] = "retired"
        elif f.field.type == "choice":
            labels = {v["value"]: v.get("label") for v in f.field.options.get("values", [])}
            display[f.name] = translated(labels.get(value), language)
    return display, state


_PERSON = "sys.person"
_BY_PERSON = ("created_by", "updated_by", "deleted_by")


def _people(found: list[Record]) -> set[UUID]:
    return {v for r in found for name in _BY_PERSON if isinstance(v := r.values.get(name), UUID)}


def _who(record: Record, people: dict[UUID, tuple[Any, bool]]) -> dict[str, Any]:
    """Who made, changed or deleted it, named: system fields, so not among the table's own
    references."""
    return {
        name: people[person][0]
        for name in _BY_PERSON
        if isinstance(person := record.values.get(name), UUID) and person in people
    }


PREVIEW_RECORDS = 10  # named per group in a delete's preview


async def deletion_preview(records: Records, key: str, sys_id: UUID) -> dict[str, Any]:
    """What deleting this record would do, for the person to confirm: per table and field, how
    many records and the first of them they may read, named. `fingerprint` confirms it."""
    plan = await records.deletion(key, sys_id)
    language = records.situation.language

    async def group(p: Pointing) -> dict[str, Any]:
        names = await named(records, records.catalog, p.table.key, set(p.ids[:PREVIEW_RECORDS]))
        allowed = await records.may(p.table, p.ids, ("delete",))
        return {
            "table": p.table.key,
            "label": translated(p.table.label_plural or p.table.label, language),
            "label_one": translated(p.table.label, language),
            "field": p.field.name,
            "field_label": translated(p.field.field.label, language),
            "count": len(p.ids),
            "records": [
                {"sys_id": str(i), "name": names[i][0]}
                for i in p.ids[:PREVIEW_RECORDS]
                if i in names
            ],
            # Each one must be deletable by the person too, or the delete is refused.
            "may_delete": all(allowed.get(i, {}).get("delete", False) for i in p.ids),
        }

    return {
        "fingerprint": plan.fingerprint,
        "changes": plan.changes,
        **{
            kind: [await group(p) for p in found]
            for kind, found in (
                ("cascade", plan.cascade),
                ("clear", plan.clear),
                ("blocked", plan.blocked),
                ("kept", plan.kept),
            )
        },
    }


async def annotate(records: Records, table: TableDef, found: list[Record]) -> list[dict[str, Any]]:
    """The records as JSON, each with `_display` (references and choices named) and `_access`
    (what the person may do with it)."""
    bodies = [records.to_json(table, r) for r in found]
    language = records.situation.language
    references: dict[str, set[UUID]] = {}
    for f in table.active_fields():
        for r in found:
            target = target_of(records.catalog, table, f, r)
            if target is not None and isinstance(r.values.get(f.name), UUID):
                references.setdefault(target, set()).add(r.values[f.name])
    if people := _people(found):
        references.setdefault(_PERSON, set()).update(people)
    names = {
        key: await named(records, records.catalog, key, ids) for key, ids in references.items()
    }
    allowed = await records.may(table, [r.sys_id for r in found], _PER_RECORD)
    own_paths = (
        await paths(records, table, {r.sys_id: (r, False) for r in found})
        if table.parent_field is not None and table.display is not None
        else {}
    )
    for record, body in zip(found, bodies, strict=True):
        display, state = _names_of(records.catalog, table, record, names, language)
        display |= _who(record, names.get(_PERSON, {}))
        body["_display"] = display
        if state:
            body["_reference_state"] = state
        body["_access"] = allowed.get(record.sys_id, dict.fromkeys(_PER_RECORD, False))
        if record.sys_id in own_paths and table.display is not None:
            # Its name where it's shown is its path; the field itself keeps its own value.
            body["_path"] = own_paths[record.sys_id][0]
            display[table.display] = own_paths[record.sys_id][0]
    return bodies
