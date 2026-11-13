# Access

Who may do what. People are members of groups, groups hold roles, and roles are made of grants.
A person gets a role only through a group. Nothing is allowed by default.

## Records

| Table | Holds |
|---|---|
| `sys.person` | Everyone who signs in or acts in the system: name, email, kind (employee, external, service account), active |
| `sys.group` | Groups of people; a group can be marked as one work is assigned to |
| `sys.membership` | A person in a group |
| `sys.role` | Roles: declared by modules (`task.editor`), or custom ones built in the app |
| `sys.role_include` | A role including another role's grants |
| `sys.grant` | One operation (read, create, write, delete, restore) on one table, optionally only for records matching a condition and only for some fields |
| `sys.group_role` | A role held by a group, optionally narrowed by a condition for this group only |

An inactive person has no access at all, whatever their groups.

## Declaring roles

```python
Role("task.editor", "Task editor", grants=(
    Grant("task.task", ("read", "create", "write")),
    Grant("task.task", ("delete",), condition={"field": "opened_by", "op": "eq", "value": "@me"}),
    Grant("task.task", ("read",), fields=("number", "state")),
), includes=("task.reader",))
```

Conditions use the filter language (`records.md`), including `@me` and `@my_groups`. Roles are
checked before they're written: the tables, operations and fields must exist, conditions must
build, and a role may not include itself. Writing a module's roles again brings their records to
the declaration and retires the roles it no longer declares. Custom roles aren't touched.

## How access is applied

The person's grants are worked out for each request, and the records service applies them in
SQL:

- **Deny by default.** No grant for an operation on a table: 403. A record no grant reaches: 404,
  as if it didn't exist.
- **Grants add up.** If any of a person's grants allows an operation, it's allowed on the records
  their conditions match.
- **Fields count per record.** A field shows on a record only if a grant that covers the field
  also matches that record. A grant for every field of one's own record never opens those fields
  on other records that another grant lets one see. Fields a person may not read are absent from
  the answer.
- **Hidden stays hidden.** A field a person may not read on a record can't be used to find it:
  filters treat it as not matching, sorting treats it as empty. Following a reference
  (`owner.name`) reaches only records and fields the person may read.
- **Changes**: the record must be one the operation is allowed on, and each changed field must be
  covered on it. A new record must match a `create` grant for each field given, otherwise it's
  undone and refused.
- **Undo** needs the rights of a change today (`records.md`).
- **A narrowed group role** applies its condition on top of the role's grants, for that group's
  members only.
- **A grant that can't be built any more** (its condition names a retired field) grants nothing.
- The system itself (installing, upgrading) works with unrestricted access.

## Reading along references

A module's database role writes only its own schema. When a module declares a reference into
another module's table, its role may read that table, and every table further references reach,
so filters along references work. Which of those records and fields anyone sees is still decided
by the grants.

## The core's roles

| Role | Grants |
|---|---|
| `sys.admin` | People: everything. Groups, roles, grants and metadata: read |
| `sys.access_admin` | People: read. Groups, memberships, roles, included roles, grants, group roles: everything |

Both are marked powerful. A new installation has an **Administrators** group holding both, and
the system's own people (System, Database).
