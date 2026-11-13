# Records and the audit log

Rules every table follows. The database enforces them, so they hold for every way in, SQL outside
the app included.

## System fields

Every table has these fields. The database fills them; values sent by a client are replaced.

| Field | Meaning |
|---|---|
| `sys_id` | ID (UUIDv7) |
| `created`, `created_by` | When and by whom the record was made |
| `updated`, `updated_by` | When and by whom it was last changed |
| `source` | Where it came from: the channel of the request that made it (`ui`, `api:token:<name>`, `db:<role>` …) |
| `deleted`, `deleted_by` | Soft deletion |

`sys_id`, `created`, `created_by` and `source` never change; trying to is refused. An update
that changes no other field leaves `updated` and `updated_by` as they were.

## No real deletes

`DELETE` and `TRUNCATE` are refused. Deleting a record sets `deleted` (the database sets the
time and who); restoring clears it. `p42cloud check` reports any role the app works as that has
the right to delete rows.

## The audit log

`sys.audit_log` gets one entry per changed record, written by a trigger:

- the table and record, the operation (`insert`, `update`, `delete`, `restore`), who, the
  channel and the request ID;
- the changed fields only, as `[old, new]`; fields marked secret are stored as `"(changed)"`,
  never with their values;
- an update that changes nothing visible writes no entry.

Who, channel and request ID come from the request's context. Without one (SQL outside the app),
the entry names the database person and the channel `db:<role>`.

The log can only be added to: changing, deleting or truncating entries is refused, the owner
role included. It is split into monthly partitions (UTC months), created three months ahead by
`p42cloud upgrade`. Changes can't be saved in a month that has no partition, so `p42cloud check`
reports a missing one up to two months ahead: run `p42cloud upgrade` when it does.

## Hash chain

When a transaction commits, each of its entries gets the next number and a hash over the
previous hash and all its fields. `p42cloud audit verify` recomputes the chain and reports an
entry that was changed, removed or inserted, even by someone who switched the triggers off.

Someone who controls the database can also rewrite the whole chain so that it verifies again.
To find that, keep the head that `audit verify` prints (`<number>:<hash>`) somewhere outside the
database, and pass it later with `--anchor`: a rewritten chain no longer has that entry.

## Engine steps

The database objects behind these rules are numbered steps, applied once each by
`p42cloud upgrade` (as the owner role) and recorded in `sys.engine_step`.
