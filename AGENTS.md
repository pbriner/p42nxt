# Instructions for coding agents and contributors

Read this first, whatever agent you are. `CLAUDE.md` only points here.

## Where to read what

| You need | Read |
|---|---|
| The principles, layers and rules for code | `docs/architecture.md` |
| A built core feature | `docs/features/<feature>.md` |
| A module's design | `modules/<id>/README.md` |

Docs describe what exists. A new module documents itself in its `README.md`, starting from
`modules/_template/README.md`.

## Rules

- **Everything is a database record.** Every definition (tables, fields, roles, views, widgets,
  workflows, jobs, help, modules) ends up as a record, and the runtime reads the records, not
  the code that delivered them.
- **API first, and we only use our own API.** Build the HTTP API first. The browser app, the
  command line and integrations use only the public API; never add an endpoint or a parameter
  just for the UI.
- **The core is a module, and every module stands alone.** Each module has its own version and
  can be installed, upgraded, enabled, disabled and removed on its own; only the core and the
  modules it depends on (core dependencies) can't be disabled or removed.
- **Changing a module means releasing it**: raise its version in `pyproject.toml` and add a
  `CHANGELOG.md` entry (CI checks it).
- **Respect the layers.** A layer imports only the layers below it. No imports inside functions
  to get around a cycle: move the code or pass the dependency in.
- **Modules import only `p42cloud.sdk`**, never each other. Another module's data goes through its
  API, with the SDK's in-process client. The engine never names a module.
- **Declare tables, don't migrate them.** Tables and fields are declared in the manifest; the
  engine builds the database from them. Migrations only move data.
- **Setup stays easy.** Every setting has a safe default; a change that needs manual setup steps
  isn't done. The fresh-install test must pass.
- **Secure by default.** Check every change against the OWASP Top 10 (`docs/architecture.md`,
  principle 6). Safe behaviour needs no setting; anything that weakens it is explicit, visible
  and audited. When unsure, fail closed.
- **Never delete for real**: soft deletion and the audit trail are enforced in the database.
- **Every route declares its permission**; nothing is reachable by default.
- **No secrets or private files** in commits (`.env`, keys, dumps, `*.local.md`).

## Done means

The repository is one uv workspace (`uv sync --all-packages` at the root). All of these, in
`backend/`:

1. `uv run ruff check .` and `uv run ruff format --check .`
2. `uv run pyright` (strict, 0 errors)
3. `uv run lint-imports` (all contracts kept)
4. `uv run pytest` (this also checks that modules import only the SDK)

And in every changed module's folder (`modules/<id>/`): `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyright` and `uv run pytest`.

Once they exist, also the frontend build, the Playwright tests and the fresh-install test. CI
runs the same checks (`.github/workflows/checks.yml`).

## Writing

- Interface, help and docs in plain English: short sentences, everyday words, no internal names
  in what users read.
- Comments explain why, not what.
- Commit messages: a subject saying what changed, a body with the why. Agents commit only when
  asked and never push.
