# The app image: the engine, the core module and the p42cloud command line.
FROM python:3.14-slim

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
RUN uv sync --frozen --no-dev --package p42cloud

ENV PATH=/app/.venv/bin:$PATH
USER 10001
ENTRYPOINT ["p42cloud"]
