"""Settings of an installation, read from P42_* environment variables (or a .env file).

Every setting has a safe default, except the database addresses, which the installer writes.
Unsafe combinations are refused at start, not discovered later.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

type Environment = Literal["development", "test", "qa", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="P42_", env_file=".env", extra="ignore")

    environment: Environment = "production"
    # The app connects with this role. It owns nothing and switches to a module's role for
    # each piece of work (docs/architecture.md, Runtime).
    database_url: str
    # The owner role creates and changes tables. Only installing and upgrading use it.
    owner_database_url: str
    master_key_file: Path | None = None

    @model_validator(mode="after")
    def _refuse_unsafe(self) -> Self:
        app, owner = make_url(self.database_url), make_url(self.owner_database_url)
        if app.username == owner.username:
            raise ValueError("the app and the owner must be different database roles")
        if app.database != owner.database:
            raise ValueError("the app and the owner must use the same database")
        if self.environment == "production":
            for url in (app, owner):
                if url.password and "dev-only" in url.password:
                    raise ValueError("development passwords are not allowed in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  (values come from the environment)
