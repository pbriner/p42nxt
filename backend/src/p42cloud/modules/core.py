"""The core module (`sys`): put together from the features' parts, installed like any module."""

from importlib.metadata import version
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data.access import SHARE
from p42cloud.data.external_ids import EXTERNAL_ID
from p42cloud.modules import models as module_models
from p42cloud.modules.install import compose as connect
from p42cloud.modules.install import install_modules
from p42cloud.modules.manifest import CORE as CORE_ID
from p42cloud.modules.manifest import Module
from p42cloud.security.access import hooks as access_hooks
from p42cloud.security.access import manifest as access
from p42cloud.security.access import models as access_models
from p42cloud.security.people import models as people
from p42cloud.security.providers import hooks as provider_hooks
from p42cloud.security.providers import models as providers
from p42cloud.security.sign_in import hooks as sign_in_hooks
from p42cloud.security.sign_in import models as sign_in
from p42cloud.security.tokens import hooks as token_hooks
from p42cloud.security.tokens import models as tokens

# The repository's docs/features, which is also where the image puts them: the same place
# relative to this file in both.
DOCS = Path(__file__).resolve().parents[4] / "docs" / "features"

CORE = Module(
    id=CORE_ID,
    name="Core",
    version=version("p42cloud"),
    tables=(
        *people.TABLES,
        *access_models.TABLES,
        SHARE,
        EXTERNAL_ID,
        *sign_in.TABLES,
        *tokens.TABLES,
        *providers.TABLES,
        *module_models.TABLES,
    ),
    roles=tuple(access.ROLES),
    hooks=(
        *access_hooks.TABLE_HOOKS,
        *sign_in_hooks.TABLE_HOOKS,
        *token_hooks.TABLE_HOOKS,
        *provider_hooks.TABLE_HOOKS,
    ),
    docs=DOCS if DOCS.is_dir() else None,
    description="People, access, sign-in and the platform itself.",
    seed=access.seed,
)


def compose() -> None:
    """Connects the core's hooks (for a process that installs nothing)."""
    connect([CORE])


async def install_core(engine: AsyncEngine) -> list[str]:
    """Installs or upgrades the core alone (tests, and installations without modules)."""
    return await install_modules(engine, [CORE])
