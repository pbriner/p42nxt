# Records

One service reads and changes the records of every table, from the catalog alone
(`backend/src/p42cloud/data/records.py`). The HTTP API is a thin layer over it.

Each operation runs as the database role of the table's module, so a module's records are only
ever written by that module's role. What each person may read and change comes from their grants
(`access.md`).

## Reading and changing

| Operation | What it does |
|---|---|
| `read` | One record, deleted or not |
| `list` | One page of records (below) |
| `create` | Checks every value, fills in defaults, needs every required field |
| `update` | Changes the given fields; needs the record's ETag |
| `delete` | Soft deletion; needs the ETag. The database sets who and when |
| `restore` | Clears the deletion |

- **Values are checked by their field types**, and all problems come back at once as field errors
  (422). Unknown fields, system fields and retired fields are refused.
- **The database checks again**, and its refusals come back as field errors too: `not_unique`,
  `no_such_record`, `required`, `invalid`.
- **ETags**: every record has one, a hash of its values. A change without one is refused (428).
  A change based on an older version is refused too (412), and the answer carries the current
  record. An update that changes nothing keeps the ETag.
- A deleted record can't be changed until it's restored.
- **Metadata** (`sys.table`, `sys.field`, `sys.field_type`) can be read and listed like any table,
  but change only through declarations (`tables.md`).
- Values are returned as stored. As JSON, exact numbers and times are text: `"199.50"`,
  `"2026-11-13"`, `"P1DT2H"`.

## Lists

```python
await records.list("shop.product",
    where={"and": [{"field": "state", "op": "in", "value": ["draft"]},
                   {"field": "category.name", "op": "eq", "value": "desks"}]},
    sort=["-price", "name"], limit=50)
```

- **Paging by cursor**: each page gives the cursor of the next one. Pages stay fast and don't skip
  or repeat records when others change the table. A cursor only works for the same sort order.
- `limit` is from 1 to 500 (default 50).
- **Totals** are exact up to 10,000, otherwise "more than 10,000"; `count=False` skips counting.
- **Sort** by up to 5 of the table's own fields, `-` for descending. Empty values come last. Ties
  are broken by `sys_id`, so the order is always the same.
- Deleted records aren't listed; `deleted=True` lists only them.

## The filter language

A condition is JSON: `{"field", "op", "value"}`, or a group `{"and": [...]}`, `{"or": [...]}`,
`{"not": ...}`. An empty `and` matches everything; an empty `or` matches nothing.

| Fields | Operators |
|---|---|
| Every filterable field | `eq`, `ne`, `empty`, `not_empty` |
| Text, email, URL, phone, host name, colour, icon | `contains`, `not_contains`, `starts_with`, `ends_with`, `in`, `not_in` |
| Translated text | as text, without `in` / `not_in` |
| Numbers, durations, dates, dates and times | `lt`, `lte`, `gt`, `gte`, `between` (`[from, to]`, both included) |
| Choices, references | `in`, `not_in` |

- **Text** comparisons ignore case and accents: `muller` finds `Müller`. Translated text is
  compared in the person's language, falling back to English.
- **`ne`, `not_in` and `not_contains` keep records without a value**: "state isn't sold" includes
  records with no state. Add `not_empty` to exclude them. `not` is the exact opposite of what it
  wraps.
- **Following references**: `category.name`, up to 3 steps.
- **Dates and times**: a date compared with a date-and-time field means the whole day.
  Variables: `@today`, `@now`, relative ones like `@today-7d`, `@now+2h` (`d`, `w`, `m`, `y`, `h`),
  and `@start_of_week`, `@end_of_month` (also `quarter` and `year`). A date and time needs a time
  zone. Days are UTC for now.
- **`@me`** is the person the list is for.
- System fields can be filtered and sorted too: `created`, `created_by`, `updated`, `updated_by`,
  `source`, `deleted`, `deleted_by`, `sys_id`.
- Markdown and JSON fields can't be filtered.
- **Limits**: at most 50 conditions, groups nested at most 4 deep.
- **Anything unknown is refused**, never ignored: a field, an operator, a value of the wrong
  type. Values are always bound parameters; names come from the catalog.

## Undo

`undo(conn, catalog, request=…)` undoes everything one request did, newest first. `entry=…` undoes
one audit entry.

- **Undo is a new change**: audited under its own request ID, with the channel
  `<channel>:undo:<what was undone>`. History is never rewritten.
- Undoing a create deletes the record, undoing a delete restores it, and undoing an update sets
  the fields back.
- **What can't be undone is skipped and listed with the reason**: a field changed again since, a
  secret (its old value isn't kept), a retired field, a record deleted since, or anything the
  database refuses (a name taken again). Everything else is still undone.
- Values come back exactly as the audit log holds them.
- Undoing an undo does the change again.
