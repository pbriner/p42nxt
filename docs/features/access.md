# Access

Who may do what. People are members of groups, groups hold roles, and roles are made of grants.
A person gets a role only through a group. Nothing is allowed by default.

## Records

| Table | Holds |
|---|---|
| `sys.person` | Everyone who signs in or acts in the system: name, email, kind (employee, external, service account), active |
| `sys.group` | Groups of people; a group can be marked as one work is assigned to, and can have a rule |
| `sys.membership` | A person in a group; marked when it comes from the group's rule |
| `sys.role` | Roles: declared by modules (`task.editor`), or custom ones built in the app |
| `sys.role_include` | A role including another role's grants |
| `sys.grant` | One operation (read, create, write, delete, restore) on one table, optionally only for records matching a condition and only for some fields |
| `sys.group_role` | A role held by a group, optionally narrowed by a condition for this group only |
| `sys.share` | A personal record shared with a person: to read or to change, optionally until a time |

An inactive person has no access at all, whatever their groups.

## Groups with a rule

A group with a rule (the filter language, on the person) has as members every active person the
rule matches, for example `{"field": "kind", "op": "in", "value": ["external"]}`.

- **Kept in step at once**, in the same transaction as the change: a person's fields changing, a
  person becoming inactive, the group's rule changing. Someone who moves or leaves loses the
  group's roles immediately. Every move is an audited membership record.
- Members of a group with a rule come only from the rule; they can't be added or removed by hand.
  A group whose rule is removed keeps its members, kept by hand from then on.
- **Rules can't be gamed**: they use only the person's own fields, and none people change about
  themselves (name and email). `@me`, `@my_groups` and `@person` are refused.
- Service accounts are never members of a group with a rule.
- Changes made with SQL outside the app don't recalculate memberships.

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
- **A switched-off module's** roles count for nobody, and grants on its tables give nothing, in
  custom roles too.
- The system itself (installing, upgrading) works with unrestricted access.

## Reading along references

A module's database role writes only its own schema. When a module declares a reference into
another module's table, its role may read that table, and every table further references reach,
so filters along references work. Which of those records and fields anyone sees is still decided
by the grants.

## No escalation

Nobody can give themselves more rights than they were given:

- **Every change to access** (groups, memberships, roles, grants, group roles) needs the second
  factor given in the last 15 minutes (`sign-in.md`).

- **Powerful roles** are given to a group, or taken from it, only by members of a group holding
  `sys.admin`. The same goes for adding or removing members of a group holding a powerful role,
  changing its rule, or deleting it. So an access administrator can't join the Administrators
  group.
- **Roles of modules are read-only**: they change only with the module.
- **Custom roles** are built in the app by access administrators: always custom, never declared.
  A custom role holds only grants for a table and operation that some module role grants, with
  its own conditions and fields.
- A custom role that copies a power only powerful roles have, or includes a powerful role, is
  **powerful itself** (worked out, not set by hand). While a group holds it, only administrators
  may change it into one.

## Personal records

A table can be declared personal by naming its owner field (a reference to a person):

```python
Table("budget", "Budget", {..., "owner": Reference("Owner", "sys.person", required=True)},
      owner="owner")
```

- **Only the owner, and people they share a record with, see it.** No group, role or
  administrator overrides that; grants still decide what they may do with it.
- A new record must be the creator's own.
- **Sharing**: the owner shares a record with a person, to read or to change, optionally until a
  time. Only the owner shares, changes or ends a share. People see the shares they made and the
  ones made with them.
- Someone a record is shared with, even to change it, can't change whose it is.

## The core's roles

| Role | Grants |
|---|---|
| `sys.admin` | People and sign-in providers: everything. Groups, roles, grants, metadata and external IDs: read. Sessions and API tokens: read and end |
| `sys.access_admin` | People and metadata: read. Groups, memberships, roles, included roles, grants, group roles: everything |
| `sys.user` | People's name, kind and whether active; groups' names: read. Their own shares: everything. Their own sessions and API tokens: read and end |

`sys.admin` and `sys.access_admin` are powerful. A new installation has an **Administrators**
group holding both, an **Everyone** group (rule: every employee and external person) holding
`sys.user`, and the system's own people (System, Database).
