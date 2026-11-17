"""The module's tests run against a fresh installation of the core and this module."""

import pytest
from p42cloud.sdk import Module

from p42cloud_partners import module

pytest_plugins = ["p42cloud.sdk.testing"]


@pytest.fixture(scope="session")
def p42_modules() -> list[Module]:
    return [module]
