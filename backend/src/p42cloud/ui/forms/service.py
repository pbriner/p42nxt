"""Writing a module's form views into records; tables without one get one made from fields."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from p42cloud.data.catalog import Catalog, TableDef
from p42cloud.data.conditions import field_of
from p42cloud.kernel.errors import FieldError, Problem
from p42cloud.ui.declared import label_of, sync
from p42cloud.ui.forms.models import MAX_COLUMNS, FormSection, FormView


def generated(table: TableDef) -> FormView:
    """One section with every field, in two columns."""
    fields = tuple(f.name for f in table.active_fields())
    return FormView(table.key, (FormSection(table.label.get("en", table.name), fields),))


def _problems(catalog: Catalog, module: str, form: FormView) -> list[FieldError]:
    where = f"forms.{form.table}"
    table = catalog.table(form.table)
    if table is None or table.retired or table.schema != module:
        return [FieldError(where, "unknown_table", "A form is of one of the module's tables.")]
    found: list[FieldError] = []
    for section in form.sections:
        found += [
            FieldError(where, "unknown_field", f"No field {name!r}.")
            for name in section.fields
            if field_of(table, name) is None
        ]
        if not 1 <= section.columns <= MAX_COLUMNS:
            found.append(FieldError(where, "bad_columns", f"1 to {MAX_COLUMNS} columns."))
    for related in form.related:
        other = catalog.table(related.table)
        f = field_of(other, related.field) if other else None
        if f is None or f.field.type != "reference" or f.field.options.get("table") != form.table:
            found.append(
                FieldError(
                    where, "bad_related", f"{related.table}.{related.field} doesn't point here."
                )
            )
    return found


async def write_forms(
    conn: AsyncConnection, catalog: Catalog, module: str, forms: tuple[FormView, ...]
) -> None:
    found = [p for f in forms for p in _problems(catalog, module, f)]
    declared = {f.table for f in forms}
    if len(declared) != len(forms):
        found.append(FieldError("forms", "duplicate", "One form per table."))
    if found:
        raise Problem(422, "invalid_forms", "The forms can't be used.", errors=found)
    tables = [t for t in catalog.tables.values() if t.schema == module and not t.retired]
    every = [*forms] + [generated(t) for t in tables if t.key not in declared]
    rows: list[dict[str, Any]] = [
        {
            "name": form.table,
            "table": form.table,
            "sections": [
                {"title": label_of(s.title), "fields": list(s.fields), "columns": s.columns}
                for s in form.sections
            ],
            "related": [{"table": r.table, "field": r.field} for r in form.related] or None,
            "related_auto": form.related_auto,
        }
        for form in every
    ]
    await sync(conn, "sys.form_view", module, rows, frozenset({"sections", "related"}))
