"""Module names can't clash with the platform's, and a module says who makes it."""

import os

import pytest

from p42cloud.kernel.config import Settings
from p42cloud.kernel.secrets import Sealer
from p42cloud.modules.manifest import CORE, MODULE_ID, RESERVED, Module
from p42cloud.web.app import create_app


def _codes(module: Module) -> set[str]:
    return {e.code for e in module.problems()}


@pytest.mark.parametrize("name", ["tables", "languages", "people", "core", "p42", "admin"])
def test_the_platforms_names_are_kept(name: str) -> None:
    assert "reserved" in _codes(Module(id=name, name="X", version="1.0.0", publisher="someone"))


def test_sys_is_the_platforms_alone() -> None:
    assert "reserved" in _codes(Module(id=CORE, name="X", version="1.0.0", publisher="someone"))


def test_a_module_says_who_makes_it() -> None:
    needs = {CORE: ">=0.1,<1"}
    # Empty loads (modules from before publishers); a malformed one doesn't.
    nobody = Module(id="crm", name="CRM", version="1.0.0", depends_on=needs)
    assert _codes(nobody) == set()
    odd = Module(id="crm", name="CRM", version="1.0.0", publisher="Acme Inc", depends_on=needs)
    assert "bad_publisher" in _codes(odd)
    made = Module(id="crm", name="CRM", version="1.0.0", publisher="acme", depends_on=needs)
    assert _codes(made) == set()


def test_no_module_can_take_an_address_of_the_cores_api(upgraded: Settings) -> None:
    """Modules live at /api/v1/<id>/…: every first part of the core's own addresses that could
    be an ID is kept, so a module never shadows the core, now or after a new core route."""
    app = create_app(upgraded, sealer=Sealer(os.urandom(32)), upgrade=False)
    first = {
        str(getattr(r, "path", "")).split("/")[3]
        for r in app.routes
        if str(getattr(r, "path", "")).startswith("/api/v1/")
    }
    could_be_ids = {s for s in first if MODULE_ID.match(s)}
    assert could_be_ids - RESERVED == set()
