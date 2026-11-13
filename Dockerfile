# The app image: the engine, the core module and the p42cloud command line.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# The package's license files are referenced as ../LICENSE and ../NOTICE.
COPY LICENSE NOTICE /
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --locked --no-dev

ENV PATH=/app/.venv/bin:$PATH
USER 10001
ENTRYPOINT ["p42cloud"]
