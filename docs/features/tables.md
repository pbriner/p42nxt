# Tables and fields

Tables are declared once. The declaration is written into metadata records, and the engine builds
the database from those records.

## Declaring a table

```python
Table("product", "Product", {
    "name": String("Name", max_length=40, required=True, unique=True),
    "price": Decimal("Price", precision=10, scale=2),
    "state": Choice("State", {"draft": "Draft", "sold": "Sold"}),
    "category": Reference("Category", "shop.category"),
}, unique_together=(("name", "category"),))
```

A module's tables live in the module's own schema (`shop.product`), and only the module's database
role may write them. Every table also gets the system fields and the record rules
(`records-and-audit.md`).

Field names use `a-z`, `0-9` and `_`, at most 40 characters. They can't start with `sys_` or
reuse a system field's name. Declarations are checked before anything is written, and every
problem is reported at once.

Options every field takes: `required`, `unique`, `default`, `help`, `personal` (personal data).

## Field types

One file per type in `backend/src/p42cloud/data/types/`. Each type decides the column, the checks
the database makes, how a value is checked and stored, and which changes keep every value.

| Type | Stored as | Checked |
|---|---|---|
| `String(max_length)` | `varchar(n)` | Length |
| `Text`, `Markdown` | `text` | |
| `TranslatedText` | `jsonb` | One text per language code (`en`, `de-CH`); plain text becomes `{"en": …}` |
| `Email`, `Url`, `Phone`, `Hostname`, `Color`, `Icon` | `text` (`varchar(253)` for host names) | Format, in the app and in the database. Web addresses only `http` and `https` |
| `Integer` | `bigint` | Whole numbers only |
| `Decimal(precision, scale)` | `numeric(p,s)` | Rounded half away from zero, as the database does; never a float |
| `Percent` | `numeric(12,6)` | Stored as a fraction (0.05 is 5 %) |
| `Boolean` | `boolean` | |
| `Date`, `DateTime`, `Duration` | `date`, `timestamptz`, `interval` | A date and time needs a time zone and is stored in UTC. Durations are ISO 8601 without years or months (`P1DT2H30M`) |
| `Choice(values, tones=)` | `text` | One of the declared values (`a-z`, `0-9`, `_`), each with a label. `tones` makes some stand out in lists: `primary`, `tertiary`, `error` or `muted` (struck through), in the theme's colours |
| `Reference(table)` | `uuid` | The record exists (foreign key, indexed). `key=` fills it from another field instead (below) |
| `AnyRecord(table_field=)` | `uuid` | A record of any table; `table_field` names the field saying which (a reference to `sys.table`, or a table's name), and gives the linked record a related list |
| `Json` | `jsonb` | Valid JSON |

### References filled from a key

Records that code goes by name (a run names its job, a list view its table) link the record the
name stands for with a reference filled from that key:

```python
"job": String("Job name", max_length=120, required=True),
"job_ref": Reference("Job", "sys.job", key="job"),            # the job whose name it is
"table_ref": Reference("Table", "sys.table", key="table", match="schema.name"),
```

It works both ways. A record that names its target gets the link: the active record whose key
(`match`, `name` by default; fields joined by dots) is the name, or none. A record whose link is
picked (a new one sent without the name, or a changed link with the name left as it was) gets
the name from the picked record. The database does it on every write, whatever wrote it; the
records service looks the name up first, so the table's checks see it, and a required name is
satisfied by its picked link. Forms show the link and hide the name. Records there already are linked when the reference is added. Like any
reference, it gives the target's form a related list.

### Read-only fields

`read_only=True` on a field (any type) marks what the platform or the module sets: a check's
result, a run's state. Forms show it but don't edit it, and leave it out of a new record's form.
It's a hint for forms: what protects such a field is the table's own checks (a connection's
health comes from its check only).

## What the database enforces

- **Required**: no *active* record may leave the field empty. Deleted records may, so retiring a
  field or soft-deleting a record never fails on it.
- **Unique**: among active records. A deleted record's value can be used again.
- Choices, formats and references are checked by the database too, so they hold for SQL outside
  the app.

## Changing a table

Declare the table again. The engine compares the records with the database and applies only the
difference, in the same transaction as the change of the records: either both change or neither
does.

| Change | What happens |
|---|---|
| Add a table or field | Created. A new field with a default gets it on the records already there, so a new required field can be added to a table with data |
| Label, help, default, more choices | Records updated |
| Longer text, text to long text, whole number to decimal, more digits | Column widened |
| A type change that could lose a value (shorter, fewer choices, text to number) | Refused |
| Make a field required | Only if no active record leaves it empty; otherwise refused |
| Remove a field or table from the declaration | **Retired**: its record is marked deleted, the column and data stay; a retired field is no longer required or checked |
| Declare a retired field or table again | Back, with its data |

A table that a reference still points to can't be retired.

The engine also refuses to narrow a column if a metadata record asks for it directly. Its
constraints and indexes are named `p42_<field>_<kind><hash>`, after their definition.

## Administrators' own tables

Administrators build tables in the app (`POST /api/v1/tables`), in the schema `u` (`u.<name>`),
with the same field types and the same rules as tables modules declare.

- Their records and the database change together, as the owner role, in one transaction.
- Only administrators may, with the second factor given in the last 15 minutes (so not with an
  API token).
- Each table gets two roles: `u.<name>.reader` (read) and `u.<name>.editor` (everything). They're
  given to groups like any role, and custom roles may copy their grants. Being an administrator
  doesn't make one a reader of every table.
- Retiring one (`DELETE /api/v1/tables/<name>`) hides it and retires its roles; the data stays.

## Display field

A table names the field that names its records where they're referenced (`display=` in the
declaration). Without one: `name` if the table has it, else its first text field.

## Hierarchies

A table whose records form a tree names the field that holds each record's parent:

```python
Table("center", "Cost center", {
    "name": String("Name", max_length=80, required=True),
    "parent": Reference("Part of", "costing.center"),
}, parent="parent")
```

The parent field is a reference to the same table and not required: records without one are at
the top. The table's record of `sys.table` names it (`parent_field`), and the table's description
in the API says `parent`.

- **The database keeps it a tree.** A record can't be under itself or one of its own, nor more
  than 30 levels deep ("A record can't be under itself or one of its own"). It can't be put
  under a removed record, and a record with active records under it isn't removed ("Records are
  under it: move or remove them first"). Moves in one table wait for each other, so two at once
  can't make a loop.
- **`within`** filters a reference into a hierarchy (or a hierarchy's own `sys_id`) for one or
  more records and everything under them, at any depth: `{"field": "center", "op": "within",
  "value": "<id>"}`. What's under them is found with the person's rights.
- **Paths.** Wherever a record of a hierarchy is named, it's named by its path from the top,
  `House › Ground floor › Heating`, through the records above it the person may read. A record's
  own name in lists (`_display`) is its path too, and `_path` holds it; the field keeps its own
  value.

## Metadata tables

| Table | Holds |
|---|---|
| `sys.field_type` | One record per field type this version has |
| `sys.table` | One record per table: module (schema), name, labels, unique field sets |
| `sys.field` | One record per field: table, name, type, labels, help, options, required, unique, default, personal data, position |

They describe themselves: `p42cloud upgrade` builds them from their declarations and then writes
their own records. Changes to them are audited like any other record.

## The catalog

The catalog (all tables and fields) is read from these records, never from code. Each process
keeps it in memory and drops it as soon as PostgreSQL announces a change to the metadata tables
(`LISTEN` / `NOTIFY` on `p42_metadata`). If that connection is lost, the process reads the records
again for every use rather than trust an old copy.
