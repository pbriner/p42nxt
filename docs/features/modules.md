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
| `id` | 2 to 16 of `a-z` and `0-9`, starting with a letter. It names the module's schema and database role. `u` is kept for admins' own tables, `search` for the search route |
| `version` | The module's version (`1.4.0`) |
| `depends_on` | The modules it needs, with the versions it works with. Every module depends on `sys` |
| `tables`, `roles` | Its tables and roles, declared (`tables.md`, `access.md`) |
| `hooks` | Code to run when its own tables' records change (`records.md`). A module hooks only its own tables |
| `docs` | A folder of Markdown files: its documentation |
| `seed` | Records it makes once installed; run again at every upgrade, so it must do nothing twice |
| `migrations` | Code that moves data on the upgrade to a version (below) |
| `category`, `icon` | Where the module shows in the navigator (`General` and `extension` if not given) |
| `jobs`, `schedules` | Work it runs, and when (`jobs.md`) |
| `navigation`, `views`, `forms`, `actions`, `rules`, `widgets`, `themes` | Its navigator items, list views, form views, UI actions, form rules, widgets and themes (`screens.md`) |

The package names its manifest as an entry point, which is how the platform finds it:

```toml
[project.entry-points."p42cloud.modules"]
task = "p42cloud_task:module"
```

## Installing and upgrading

`p42cloud upgrade` brings the core and every installed module to their versions, except the
ones removed.

1. **Checked first**: every manifest, every dependency there in a version its range allows, and no
   modules depending on each other in a circle. If anything is wrong, nothing changes.
2. **In order**: the core first, then each module after the modules it depends on.
3. **Each module in its own transaction**, as the owner role: its database role (made the first
   time, with the app allowed to switch to it), its schema and tables, its roles, its first
   records, its documentation, and its record with the version.
4. **Never downgraded**: a version lower than the installed one is refused.

If anything in a module's upgrade fails, its transaction is rolled back: that module stays as it
was, and the ones before it are upgraded.

## Data migrations

Tables change only through their declarations; migrations only move data.

```python
Migration("1.1.0", fill_titles)  # async def fill_titles(conn): ...
```

On an upgrade, the migrations whose version lies after the installed version and up to the new
one run in version order, after the tables are adjusted and in the same transaction. A fresh
install runs none. They run as the owner role.

## Switching off and removing

`p42cloud module list | on <id> | off <id> | remove <id> | install <id>`. Nothing is ever
deleted.

- **Switched off**: the module's tables and roles count for nobody, the system included: its
  records can't be read or changed (as if the tables weren't there), its grants give nothing,
  its hooks never run. Its data and records stay, and upgrades still bring it to its new
  version; it stays off.
- **Removed**: only when it's off. Its tables, roles and documentation are retired; its data stays,
  hidden and read-only. Upgrades no longer install it, even if its package is still there.
  `install <id>` brings it back with its data, still off.
- **Protected**: the core and every module it depends on are never switched off or removed. A
  module can't be switched off while a module that's on needs it, nor removed while an installed
  one needs it, nor switched on while something it needs is off.

## Releases and updates

See `releases.md`: the version and changelog rule, signed packages, the registry and
`p42cloud module updates`.

## Records

| Table | Holds |
|---|---|
| `sys.module` | One record per installed module: ID, name, version, what it depends on, whether it's on, whether it's part of the core |
| `sys.module_update` | The updates available for installed modules (`releases.md`) |
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
