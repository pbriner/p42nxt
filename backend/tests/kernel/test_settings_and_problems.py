"""Unsafe settings are refused at start; problems follow RFC 9457."""

import pytest
from pydantic import ValidationError

from p42cloud.kernel.config import Settings
from p42cloud.kernel.errors import FieldError, Problem

APP = "postgresql+asyncpg://p42_app:secret-1@db/p42"
OWNER = "postgresql+asyncpg://p42_owner:secret-2@db/p42"


def test_safe_settings_are_accepted() -> None:
    settings = Settings(database_url=APP, owner_database_url=OWNER)
    assert settings.environment == "production"


def test_the_app_and_the_owner_must_be_different_roles() -> None:
    with pytest.raises(ValidationError, match="different database roles"):
        Settings(database_url=APP, owner_database_url=APP)


def test_development_passwords_are_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="development passwords"):
        Settings(
            database_url="postgresql+asyncpg://p42_app:p42-app-dev-only@db/p42",
            owner_database_url=OWNER,
        )


def test_a_problem_has_a_stable_type_and_code() -> None:
    problem = Problem(
        status=422,
        code="validation",
        title="Some fields aren't valid",
        errors=[FieldError("due", "date_in_past", "The due date is in the past.")],
    )
    assert problem.to_dict() == {
        "type": "/api/v1/problems/validation",
        "title": "Some fields aren't valid",
        "status": 422,
        "code": "validation",
        "errors": [
            {"field": "due", "code": "date_in_past", "message": "The due date is in the past."}
        ],
    }
