"""Settings of an installation, read from P42_* environment variables (or a .env file).

Every setting has a safe default, except the database addresses, which the installer writes.
Unsafe combinations are refused at start, not discovered later.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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
    # The address people open the app at (https://p42.example.com). Passkeys are bound to it;
    # without it they're unavailable, and authenticator apps still work.
    public_url: str | None = None
    # Where module releases are published (the registry's index.json is read from there). Off by
    # default: without it the installation never asks for updates.
    registry_url: str | None = None
    # Where the daily backup job writes the database's dumps (a folder outside the database's
    # volume). None: no backups; in production the job then fails, so administrators hear.
    backup_dir: Path | None = None
    # Where module packages installed from the registry are unpacked, one folder per module
    # (a persistent volume). None: only the modules in the image.
    modules_dir: Path | None = None
    # The reverse proxy in front of the app (addresses, comma-separated): only its
    # X-Forwarded-For is believed, so throttling per address sees the real client. None: the
    # address the request comes from.
    trusted_proxies: str | None = None
    # Requests per person, token or address and minute; more are refused for the rest of it.
    requests_per_minute: int = Field(default=600, ge=10, le=100_000)
    # Sessions end after this long without use, and after this long in any case.
    session_idle_minutes: int = Field(default=30, ge=5, le=24 * 60)
    session_max_hours: int = Field(default=12, ge=1, le=24 * 30)
    # The one-time code for the web installer (install.sh writes it). Without it, the installer
    # stays closed and the first administrator is made at the command line.
    setup_code: str | None = None

    @field_validator(
        "public_url",
        "registry_url",
        "backup_dir",
        "modules_dir",
        "trusted_proxies",
        "setup_code",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        # Compose passes a variable left empty in .env as "": that means not set.
        return None if value == "" else value

    @model_validator(mode="after")
    def _refuse_unsafe(self) -> Self:
        app, owner = make_url(self.database_url), make_url(self.owner_database_url)
        if app.username == owner.username:
            raise ValueError("the app and the owner must be different database roles")
        if app.database != owner.database:
            raise ValueError("the app and the owner must use the same database")
        production = self.environment == "production"
        if self.public_url is not None:
            _check_public_url(self.public_url, production=production)
        if production and self.registry_url and not self.registry_url.startswith("https://"):
            raise ValueError("registry_url must use https in production")
        if production:
            for url in (app, owner):
                if url.password and "dev-only" in url.password:
                    raise ValueError("development passwords are not allowed in production")
        return self


def _check_public_url(address: str, *, production: bool) -> None:
    public = urlsplit(address)
    if (
        public.scheme not in ("http", "https")
        or not public.hostname
        or public.path not in ("", "/")
    ):
        raise ValueError("public_url must be like https://p42.example.com")
    if production and public.scheme != "https" and public.hostname != "localhost":
        raise ValueError("public_url must use https in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  (values come from the environment)
