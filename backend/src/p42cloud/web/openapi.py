"""Writes the API's description to docs/api/openapi.json: `python -m p42cloud.web.openapi`.

The file is the contract: a test fails when the API differs from it, so every change to the API
shows up in review.
"""

import json
from pathlib import Path

from p42cloud.kernel.config import Settings
from p42cloud.web.app import create_app

TARGET = Path(__file__).resolve().parents[4] / "docs" / "api" / "openapi.json"


def description() -> dict[str, object]:
    # Describing the API needs no database: these addresses are never used.
    settings = Settings(
        environment="development",
        database_url="postgresql+asyncpg://p42_app@localhost/p42",
        owner_database_url="postgresql+asyncpg://p42_owner@localhost/p42",
    )
    return create_app(settings, upgrade=False).openapi()


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(description(), indent=2, sort_keys=True) + "\n")
    print(f"Wrote {TARGET}")


if __name__ == "__main__":
    main()
