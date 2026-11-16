#!/usr/bin/env bash
# An installation of an older release, with data, upgraded straight to this code: the way an
# installation that skipped releases is updated. The older release sets up its own database and
# installs its core and modules; this code then upgrades it, checks it's safe, verifies the audit
# log and reads the data back.
#
#   .github/scripts/upgrade-check.sh v0.37.0
#
# Needs the development PostgreSQL (docker compose up -d) and uv.
set -euo pipefail
from="$1"
root="$(git rev-parse --show-toplevel)"
work="$(mktemp -d)"
old="$work/old"
db="p42_upgrade_$(date +%s)_$$"
server="${P42_TEST_SERVER:-localhost:5435}"
admin="postgresql+asyncpg://p42_admin:p42-admin-dev-only@$server"

export P42_ENVIRONMENT=test
export P42_DATABASE_URL="postgresql+asyncpg://p42_app:p42-app-dev-only@$server/$db"
export P42_OWNER_DATABASE_URL="postgresql+asyncpg://p42_owner:p42-owner-dev-only@$server/$db"
export P42_ADMIN_DATABASE_URL="$admin/$db"

database() {  # create or drop the check's database, as the administrator
  (cd "$root" && uv run python - "$admin/p42" "$1" "$db" <<'PY'
import asyncio, sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def main(url: str, action: str, name: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        sql = f'CREATE DATABASE "{name}"' if action == "create" else f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
        await conn.execute(text(sql))
    await engine.dispose()
asyncio.run(main(*sys.argv[1:]))
PY
  )
}

cleanup() {
  database drop || true
  git -C "$root" worktree remove --force "$old" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

echo "::group::Install $from with data"
git -C "$root" worktree add --detach "$old" "$from" >/dev/null
database create
(
  cd "$old"
  export UV_PROJECT_ENVIRONMENT="$work/venv"
  uv sync --locked --all-packages -q
  uv run p42cloud setup-database
  uv run p42cloud upgrade
  uv run python "$root/.github/scripts/upgrade_data.py" seed "$work/seeded.json"
)
echo "::endgroup::"

echo "::group::Upgrade to this code"
cd "$root"
uv run p42cloud upgrade
uv run p42cloud check
uv run p42cloud audit verify
uv run python .github/scripts/upgrade_data.py verify "$work/seeded.json"
echo "::endgroup::"
