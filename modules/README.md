# Modules

One folder per module, self-contained and released on its own: its own package and version
(`pyproject.toml`), manifest, models, services, API, data migrations, translations, help
(Markdown), sample data, tests and `CHANGELOG.md`. A module imports only `p42.sdk`, never another
module: it reaches other modules' data through their API. Layout and rules:
`docs/architecture.md`.

Every module has its own version and is installed, upgraded, enabled, disabled and removed on
its own. The core (`sys`) is a module too: its code is in `backend/src/p42` and its docs in
`docs/`. The core and the modules it depends on are core dependencies and can't be disabled or
removed.

Each module documents itself in its `README.md`, following `_template/README.md`.
