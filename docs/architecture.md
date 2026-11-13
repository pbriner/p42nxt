# Architecture

The rules every change follows.

## Principles

### 1. Everything is a database record

Tables, fields, choices, roles, permissions, views, navigation, dashboards, widgets, workflows,
jobs, connections, help topics, themes and the modules themselves are records in the
database. There is nothing the platform knows that it can't list, search, reference, grant and
audit like data.

- **Code delivers definitions, the records are the truth.** A module declares its definitions in
  its manifest; installing or upgrading it writes them into records (marked as coming from that
  module, read-only for admins). At runtime the platform reads definitions from these records,
  never from the module's Python objects.
- **Configuration is records too.** What admins and users change (custom fields, choices, view
  overrides, personal list settings, dashboards) is a record next to the module's own, audited
  and undoable.
- **The platform describes itself with its own tables.** The metadata tables (tables, fields,
  views …) are ordinary tables: same API, same lists and forms, same access rules.

### 2. API first, and we only use our own API

Every feature is a documented HTTP API first. Everything else is a client of that API:

- **The browser app** uses only the public API. There are no endpoints just for the UI and no
  hidden parameters; whatever the UI shows, a script with the same rights can read.
- **The UI is built from records read through the API**: forms, lists, navigation and dashboards
  come from the metadata tables, so the frontend has no knowledge of particular tables.
- **The command line** uses the API too. Only the operator commands that must run before the
  app exists (installing, migrations, the master key, the first admin) work on the database
  directly.
- **Integrations and AI agents** use the same API with API tokens; nothing is reserved for our
  own clients.
- **The OpenAPI description is the contract**: generated from the code, versioned (`/api/v1`),
  and checked in CI so a change to it is visible in review.

### 3. The core is a module

The core is the module `sys`. It has a manifest and a version, and its tables, fields, roles,
views, navigation, widgets, jobs and help become records through install and upgrade, exactly
like any other module's. Modules depend on it the same way they depend on each other:
`depends_on={"sys": ">=1.2,<2"}`. Its code is the layers in `backend/src/p42` rather than a
folder in `modules/`.

What isn't a module is the **engine**: the small part that starts the app, reads the metadata
records and loads modules (`kernel`, the records engine in `data`, the loader in `modules`).
Keep the engine as small as possible: anything that can be a definition belongs in the `sys`
manifest, not in engine code.

### 4. Every module has its own version and lifecycle

Each module has its own version (semantic versioning) and is installed, upgraded, enabled,
disabled and removed on its own, as long as the version ranges in `depends_on` hold.

- **Core dependencies are protected.** The core and every module it depends on (directly or
  indirectly) are core dependencies: installed with the core, always enabled, never disabled or
  removed. They're still upgraded on their own, within the ranges the core declares. The mark
  comes from the dependency graph, never from a flag a module sets on itself.
- **A core dependency uses only the engine** (`p42.sdk`), never the core's tables or features,
  and is installed before `sys`.
- A module can't be enabled before the modules it depends on, and a module others depend on
  can't be disabled or removed while they are enabled.

### 5. Setting up is easy

Setting up an installation is one command and then a short wizard in the browser. Every
setting has a safe default; nothing has to be edited in a file to get started, and what can be
changed later is a record, changed in the app. Setup must keep working after every change: CI
installs a fresh installation with the real installer and runs a smoke test against it, and the
development environment is created by the same installer.

## Runtime

- **Processes**: one image with two roles: **web** (API and UI) and **worker** (jobs, schedules,
  outgoing deliveries). The job queue lives in PostgreSQL. An installation runs the app,
  PostgreSQL and S3-compatible file storage; nothing else.
- **Events between modules**, both declared in the manifest:
  - **During the change**: handlers run in the same transaction as the change, so validation and
    dependent changes succeed or fail together.
  - **After commit**: the change writes an event into an outbox table in the same transaction;
    the worker delivers it. At least once, so handlers must be safe to run twice.
- **One PostgreSQL schema per module**: `sys` for the core, `<module>` for each module, `u` for
  admins' tables. Each module writes through its own database role, which may only write its own
  schema.
- **Metadata cache**: each process keeps the metadata records in memory and drops what changed
  when PostgreSQL announces it (`LISTEN` / `NOTIFY`).
- **Search and files**: search is PostgreSQL's full-text search; files go to S3-compatible
  storage.
- **API versions**: `/api/v1` stands for the platform's API conventions. A breaking change to a
  module's API needs a new major version of the module; the previous major's API keeps working
  for a transition period.
- **AI agents** use an MCP server generated from the same public API and grants.

## Layers

The platform (`backend/src/p42`) is split into layers. A layer may import only the layers below
it, never the ones above. CI enforces this (`lint-imports`, contracts in
`backend/pyproject.toml`).

```
web, cli     entry points: the FastAPI app and HTTP routers, the command line
sdk          the only thing modules import (re-exports, no logic of its own)
modules      manifest types, loader, composition, versions, lifecycle
ui           views, navigation, dashboards and widgets, themes, help: resolved from their records, no HTTP
services     platform features: files, jobs, connections, notifications, workflows, approvals, imports, reference data
security     people, accounts, sign-in, sessions, tokens, roles, groups, record and field access
data         field types, table declarations, schema rules, audit, the catalog, the generic records service
kernel       settings, database engine, request context, errors, ids and time
```

`web` and `cli` sit side by side at the top and don't import each other.

### Passing things down

A lower layer never reaches up to find something. It receives it:

- The **catalog** (all tables and fields) is read by `data` from the metadata records.
  `modules` writes those records when a module is installed or upgraded; `data` never imports
  module code to find a table.
- The **current person and their permissions** are computed by `security` and put into the
  request context (`kernel`), where `data` reads them to filter records.
- **Hooks** (a service reacting to a record change) are registered by the upper layer with the
  lower one; the lower layer calls whatever is registered.

### Imports inside functions

Not allowed as a way around a cycle. If two parts need each other, one of them is in the wrong
layer or the dependency should be passed in.

## Feature packages

Each platform feature in `services`, `security` and `ui` is a package with the same parts, as
far as it needs them:

```
<feature>/
  __init__.py     what other packages may use (keep it short)
  models.py       tables
  service.py      the logic, no HTTP
  api.py          routers (imported only by web)
  manifest.py     what it adds to the sys manifest: tables, roles, views, widgets, jobs
  help/           Markdown help topics (written as files, synced into help records)
```

Tests live in `backend/tests/<layer>/<feature>/`. The `sys` manifest is assembled from the
feature manifests; there is no single large manifest file.

## Modules

```
modules/<id>/
  pyproject.toml   package p42-<id> and its version (the only place the version lives)
  src/p42_<id>/
    __init__.py    module = Module(...): the manifest, assembled from the parts below
    models.py, service.py, api.py, views.py, widgets.py, jobs.py, samples.py
  ui/              only if the module has its own screens (separate bundle)
  migrations/      data migrations only; tables come from the declarations
  translations/
  help/            Markdown
  tests/
  CHANGELOG.md, README.md
```

- **Each module is released on its own**: a change raises its version and adds a
  `CHANGELOG.md` entry (CI checks it); a tag per module (`task/v1.4.0`) builds, tests against
  its `depends_on` range, signs and publishes it.
- **A module imports only `p42.sdk`**, never another module's code. CI enforces this.
- **Other modules' data goes through their API**, with the SDK's in-process client: the same
  routes, JSON, errors and access checks as over HTTP, without the network. Inside a request it
  shares the request's transaction.
- **Whose rights**: the client acts as the person making the request. Jobs run as the module's
  own service account.
- **What needs no code at all**: a reference to another module's table is written by name
  (`Reference("partner.supplier")`), an extension of another module's table is declared by name
  in the manifest, and reacting to another module's changes is a declared event subscription.
- **Libraries**: modules use only the libraries the core provides; an extra library is declared
  and reviewed.
- The engine never names a module, and the core names modules only in its own `depends_on`.
- A module never writes to the core's or other modules' tables directly; it uses their API.

## Field types

One unit per type: `backend/src/p42/data/types/<type>.py` (storage, validation, filters,
schema) and `frontend/src/app/field-types/<type>/` (editor, display, filter). Field types are
records too; a field's record names its type, and the frontend looks the name up in its
registry. Adding a type means adding these two files and nothing else.

## Documentation

- `docs/architecture.md` (this file) holds the rules; `docs/features/<feature>.md` documents each
  built core feature; `modules/<id>/README.md` documents a module, following
  `modules/_template/README.md`.
- Docs describe what exists. Each module's and feature's `help/` is for the people using the
  app (synced into help records).

## Size and checks

- Ruff (lint and format, line length 100), pyright strict, import-linter and pytest run on every
  pull request.
- A Python or TypeScript file over 500 lines needs a reason in the review; split by concern
  first.
- Every route declares its permission; nothing is reachable by default.
