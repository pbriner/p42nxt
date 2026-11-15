"""Tables as modules declare them, checked before anything is written.

    Table("task", "Task", {
        "number": String("Number", max_length=20, required=True, unique=True),
        "state": Choice("State", {"new": "New", "done": "Done"}),
    })

A declaration becomes metadata records (metadata.py); the engine builds the database from those
records, never from the declaration.
"""

from dataclasses import dataclass, field

from p42cloud.data.catalog import Catalog, FieldDef, TableDef
from p42cloud.data.engine_steps import SYSTEM_FIELDS
from p42cloud.data.sql import NAME, SCHEMA
from p42cloud.data.types import FIELD_TYPES, Field, Invalid
from p42cloud.data.types.base import READ_ONLY
from p42cloud.kernel.errors import FieldError, Problem


@dataclass(frozen=True)
class Table:
    name: str
    label: str
    fields: dict[str, Field]
    label_plural: str | None = None
    # Sets of fields that together are unique among active records.
    unique_together: tuple[tuple[str, ...], ...] = field(default_factory=tuple[tuple[str, ...]])
    # A personal table: this field (a reference to a person) names each record's owner, and
    # only the owner and those they share a record with see it.
    owner: str | None = None
    # The field that names a record where it's referenced (`name` or the first text field
    # if not given).
    display: str | None = None


def _field_problems(table: str, name: str, declared: Field) -> list[FieldError]:
    where = f"{table}.{name}"
    if not NAME.match(name) or name.startswith("sys_") or name in SYSTEM_FIELDS:
        return [FieldError(where, "bad_name", "Not a field name (a-z, 0-9, _; not a system one).")]
    field_type = FIELD_TYPES.get(declared.type)
    if field_type is None:
        return [FieldError(where, "unknown_type", f"No field type {declared.type!r}.")]
    own = {k: v for k, v in declared.options.items() if k != READ_ONLY}
    found = [FieldError(where, "bad_option", m) for m in field_type.option_problems(own)]
    if not isinstance(declared.options.get(READ_ONLY, False), bool):
        found.append(FieldError(where, "bad_option", "read_only is true or false."))
    if not found and declared.default is not None:
        try:
            field_type.check(declared.default, declared.options)
        except Invalid as error:
            found.append(FieldError(where, "bad_default", str(error)))
    return found


def _table_problems(declared: Table) -> list[FieldError]:
    if not NAME.match(declared.name):
        return [FieldError(declared.name, "bad_name", "Not a table name (a-z, 0-9, _).")]
    found: list[FieldError] = []
    for name, declared_field in declared.fields.items():
        found += _field_problems(declared.name, name, declared_field)
    if declared.display is not None and declared.display not in declared.fields:
        found.append(
            FieldError(declared.name, "bad_display", "The display field must be one of its fields.")
        )
    if declared.owner is not None:
        owner = declared.fields.get(declared.owner)
        if owner is None or owner.type != "reference" or owner.options.get("table") != "sys.person":
            found.append(
                FieldError(declared.name, "bad_owner", "The owner must be a reference to a person.")
            )
    for together in declared.unique_together:
        unknown = [n for n in together if n not in declared.fields]
        if unknown or not together:
            found.append(
                FieldError(declared.name, "bad_unique", f"Unknown fields: {', '.join(unknown)}.")
            )
    return found


def check(schema: str, tables: list[Table]) -> None:
    """Refuses declarations that can't be built, with every problem at once."""
    if not SCHEMA.match(schema):
        raise Problem(422, "invalid_declaration", "Not a module ID.", detail=schema)
    found: list[FieldError] = []
    seen: set[str] = set()
    for declared in tables:
        if declared.name in seen:
            found.append(FieldError(declared.name, "duplicate", "Declared twice."))
        seen.add(declared.name)
        found += _table_problems(declared)
    if found:
        raise Problem(422, "invalid_declaration", "The tables can't be built.", errors=found)


def to_catalog(schema: str, tables: list[Table]) -> Catalog:
    """The catalog these declarations would give; used before the metadata tables exist."""
    check(schema, tables)
    defs = [
        TableDef(
            schema=schema,
            name=t.name,
            label={"en": t.label},
            fields=tuple(FieldDef(n, f, i) for i, (n, f) in enumerate(t.fields.items())),
            label_plural={"en": t.label_plural} if t.label_plural else None,
            unique_together=t.unique_together,
            owner_field=t.owner,
            display_field=t.display,
        )
        for t in tables
    ]
    return Catalog({d.key: d for d in defs})
