# Modules

Everything the platform does comes in modules, the core (`sys`) included. Each has its own
version and is installed and upgraded on its own (`backend/src/p42cloud/modules/`).

## A module's manifest

A module package exports one `Module`:

```python
module = Module(
    id="task", name="Tasks", version="1.4.0",
    depends_on={"sys": ">=0.1,<1"},
    tables=(TASK,), roles=(EDITOR, READER),
    hooks=(TableHook("task.task", before=check_due_date),),
    docs=Path(__file__).parent / "docs",
    seed=make_first_records,
)
```

| Part | Meaning |
|---|---|
| `id` | 2 to 16 of `a-z` and `0-9`, starting with a letter. It names the module's schema and database role. `u` is kept for admins' own tables |
| `version` | The module's version (`1.4.0`) |
| `depends_on` | The modules it needs, with the versions it works with. Every module depends on `sys` |
| `tables`, `roles` | Its tables and roles, declared (`tables.md`, `access.md`) |
| `hooks` | Code to run when its own tables' records change (`records.md`). A module hooks only its own tables |
| `docs` | A folder of Markdown files: its documentation |
| `seed` | Records it makes once installed; run again at every upgrade, so it must do nothing twice |

The package names its manifest as an entry point, which is how the platform finds it:

```toml
[project.entry-points."p42cloud.modules"]
task = "p42cloud_task:module"
```

## Installing and upgrading

`p42cloud upgrade` brings the core and every installed module to their versions.

1. **Checked first**: every manifest, every dependency there in a version its range allows, and no
   modules depending on each other in a circle. If anything is wrong, nothing changes.
2. **In order**: the core first, then each module after the modules it depends on.
3. **Each module in its own transaction**, as the owner role: its database role (made the first
   time, with the app allowed to switch to it), its schema and tables, its roles, its first
   records, its documentation, and its record with the version.
4. **Never downgraded**: a version lower than the installed one is refused.

## Records

| Table | Holds |
|---|---|
| `sys.module` | One record per installed module: ID, name, version, what it depends on |
| `sys.doc_page` | The documentation: one page per Markdown file of a module's `docs` folder, by path, titled by its first heading |

A page a module no longer ships is retired. Everyone may read the documentation and see which
modules are installed and in which version.

## The core

The core is the module `sys`, put together from the core's features. Its version is the
`p42cloud` package's. Its documentation is `docs/features` of the repository.

## Database roles

Each module's records are written by its own database role (`p42_mod_<id>`), which may only
write the module's own schema. The owner role makes these roles when it installs a module: it
may make roles (PostgreSQL's `CREATEROLE`), which lets it manage only the roles it made and never
give powers it lacks. The app's role may never make roles.
