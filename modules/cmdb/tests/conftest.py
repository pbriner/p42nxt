"""The module's tests run against a fresh installation of the core and this module."""

import pytest
from p42cloud.sdk import Module

from p42cloud_cmdb import module

pytest_plugins = ["p42cloud.sdk.testing"]


@pytest.fixture(scope="session")
def p42_modules() -> list[Module]:
    return [module]


@pytest.fixture(scope="session")
def p42_also() -> tuple[str, ...]:
    """Tasks too: a task rule opens one task per scan's finds and closes it once reviewed."""
    return ("tasks",)
