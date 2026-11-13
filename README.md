# P42cloud

One platform for small companies, with sign-in, roles and groups, audit,
generic forms, lists and dashboards, and modules that declare everything they provide. One
installation per company, set up with one command and a short wizard.

- Architecture and rules: [docs/architecture.md](docs/architecture.md)
- Rules for contributors and coding agents: [AGENTS.md](AGENTS.md)

Early development: the platform skeleton and its checks are in place; features follow.

## Layout

```
backend/     FastAPI platform (Python 3.14, uv): the engine and the core module (sys), in layers
modules/     modules, each its own package with its own version and releases
frontend/    Angular app
docs/        architecture and docs of built features
```

## Install

```bash
docker build -t p42cloud:latest .
deploy/install.sh <name>             # creates ~/.p42cloud/<name>, starts the database, sets it up
```

The installer writes the installation's folder (settings with random passwords, the master key),
makes the backup folder, sets up the database, brings it to this version (`p42cloud upgrade`)
and checks that it's safe. `p42cloud audit verify` checks the audit log's hash chain. It refuses a name
Docker already knows. The web installer follows.

## Checks

```bash
docker compose up -d                 # the dev instance p42cloud-dev: PostgreSQL 18 on localhost:5435
cd backend && uv sync
uv run ruff check . && uv run ruff format --check .
uv run pyright                       # strict
uv run lint-imports                  # layer rules
uv run pytest
```

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
