"""The module's tests run against a fresh installation of the core, Budget and what it builds
on, with Finance as well: Budget compares with Finance's bookings when it's there."""

import pytest
from p42cloud.sdk import Module

from p42cloud_budget import module

pytest_plugins = ["p42cloud.sdk.testing"]


@pytest.fixture(scope="session")
def p42_modules() -> list[Module]:
    return [module]


@pytest.fixture(scope="session")
def p42_also() -> tuple[str, ...]:
    return ("finance",)
