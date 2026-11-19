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
| `create` | Checks every value, fills in defaults (before hooks), needs every required field |
| `update` | Changes the given fields; needs the record's ETag |
| `delete` | Soft deletion; needs the ETag. The database sets who and when. Records pointing to it go as their reference declares (below) |
| `restore` | Clears the deletion, and that of the records deleted with it |

- **Values are checked by their field types**, and all problems come back at once as field errors
  (422). Unknown fields, system fields and retired fields are refused.
- **The database checks again**, and its refusals come back as field errors too: `not_unique`,
  `no_such_record`, `required`, `invalid`.
- **ETags**: every record has one, a hash of its values. A change without one is refused (428).
  A change based on an older version is refused too (412), and the answer carries the current
  record. An update that changes nothing keeps the ETag.
- A deleted record can't be changed until it's restored.
- Tables of a **switched-off module** answer as if they weren't there (404), for everyone
  (`modules.md`).
- **Metadata** (`sys.table`, `sys.field`, `sys.field_type`) can be read and listed like any table,
  but change only through declarations (`tables.md`).
- Values are returned as stored. As JSON, exact numbers and times are text: `"199.50"`,
  `"2026-11-13"`, `"P1DT2H"`.

## Deleting with links

What deleting a record does to the records pointing to it follows each reference's `on_delete`
(`tables.md`): they block it (`restrict`), go with it (`cascade`, again for the records pointing
to those), have the reference emptied (`clear`), or keep pointing to it.

- `GET /api/v1/<module>/<table>/<id>/deletion` shows it without doing it: per table and field,
  `cascade`, `clear`, `blocked` and `kept`, each with how many records, the first ten the person
  may read (named) and whether they may delete them all; and a `fingerprint`.
- A delete while anything blocks is refused (409 `in_use`, a field error per reference). A
  record under it in a hierarchy blocks it too (`has_children`).
- A delete that deletes or empties other records needs `?confirm=<fingerprint>`; without it, or
  when the records changed since, it's refused (409 `confirm`, with the current fingerprint).
  Module code deleting through the SDK confirms by itself, or passes a fingerprint.
- The person needs the right to delete each record deleted with it; each one runs its table's
  hooks. At most 1,000 records change in one delete.
- Records deleted with it are restored with it; those deleted before stay deleted. Emptied
  references stay empty.

## Hooks

Features register hooks for a table's changes (`data/hooks.py`). One run *before* a change sees
the values about to be written; it may refuse the change or add values the system sets itself
(those don't need the person's rights). One run *after* works in the same transaction, so the
change and its follow-ups succeed or fail together. A hook for every table may also *fill*
values: it runs before the tables' own before hooks, on creates and changes (form rules'
"fill when empty"). Changes made with SQL outside the records service don't run hooks.

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
| Text, email, URL, phone, host name, colour, icon | `contains`, `not_contains`, `starts_with`, `ends_with`, `in`, `not_in`, `matches` |
| Translated text | as text, without `in` / `not_in` |
| Numbers, durations, dates, dates and times | `lt`, `lte`, `gt`, `gte`, `between` (`[from, to]`, both included) |
| Choices, references | `in`, `not_in`; for references into a hierarchy also `within` |

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
- **`matches`**: a regular expression, case ignored (`agrola|top ?shop`). At most 200 characters,
  without back-references (`\1`), so matching stays fast.
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
