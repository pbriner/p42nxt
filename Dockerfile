# The app image: the engine, the core module, the browser app and the p42cloud command line.

# The browser app, built on its own; only the built files go into the image. The same files for
# every platform, so it's built on the builder's own.
FROM --platform=$BUILDPLATFORM node:24-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim
# pg_dump for the backup job, the same major version as the database (PostgreSQL's own signed
# packages: Debian's would be older).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# The repository is a uv workspace (the platform and the modules, one lock file). The image holds
# the platform only: modules are installed into an installation on their own. The files keep the
# repository's layout, so the code finds LICENSE, NOTICE and docs/ where it does there.
WORKDIR /app
COPY LICENSE NOTICE pyproject.toml uv.lock ./
COPY backend/pyproject.toml ./backend/
RUN uv sync --frozen --no-dev --package p42cloud --no-install-workspace
COPY backend/src ./backend/src
# The core's documentation, written into the documentation pages at install.
COPY docs/features ./docs/features
# The installation's compose file for this version: deploy/update.sh takes it from here.
COPY deploy/compose.yml ./deploy/compose.yml
COPY --from=frontend /app/frontend/dist/browser ./frontend/dist/browser
RUN uv sync --frozen --no-dev --package p42cloud

ENV PATH=/app/.venv/bin:$PATH
# Modules installed from signed packages: a volume here starts out owned by the app's user.
RUN mkdir -p /data/modules && chown 10001 /data/modules
USER 10001
ENTRYPOINT ["p42cloud"]
