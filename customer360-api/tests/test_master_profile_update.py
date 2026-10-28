"""Regression tests for resilient master-profile PATCH handling."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

import core.routers.identity_api as identity_router
from leo_customer360_dao.schemas.identity import MasterProfileUpdate


PROFILE_ID = UUID("5620d1ff-2fe3-4351-95a4-990174285854")


class FakeDb:
    def __init__(self):
        self.rolled_back = False

    def rollback(self):
        self.rolled_back = True


class FakeRepository:
    def __init__(self, profile, update=None):
        self.profile = profile
        self.update_values = None
        self.update_error = update

    def get_master_profile(self, _profile_id):
        return self.profile

    def update_master_profile(self, profile, values):
        self.update_values = values
        if self.update_error:
            raise self.update_error
        return profile


def _profile(**overrides):
    values = {
        "master_profile_id": PROFILE_ID,
        "tenant_id": UUID("11111111-1111-1111-1111-111111111111"),
        "domain": None,
        "attributes": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_consent_patch_preserves_required_domain_when_profile_domain_is_missing():
    profile = _profile(domain="unknown")
    repository = FakeRepository(profile)
    db = FakeDb()

    def validate_domain(_db, domain):
        if domain == "unknown":
            raise ValueError("legacy domain")

    with (
        patch("core.routers.identity_api._identity_repository", return_value=repository),
        patch("core.routers.identity_api.validate_domain_value", side_effect=validate_domain),
        patch("core.routers.identity_api.invalidate_prefix"),
        patch(
            "core.routers.identity_api.dagster_client.identity_resolution.recompute_personas",
            return_value=None,
        ),
    ):
        result = identity_router.update_master_profile(
            PROFILE_ID,
            MasterProfileUpdate(communication_preferences={"email_opt_in": True}),
            db,
        )

    assert result is profile
    assert repository.update_values["domain"] == "_"
    assert repository.update_values["communication_preferences"] == {
        "email_opt_in": True
    }
    assert db.rolled_back is False


def test_domain_constraint_error_returns_structured_validation_error():
    profile = _profile(domain="retail")
    original = SimpleNamespace(diag=SimpleNamespace(column_name="domain"))
    repository = FakeRepository(
        profile,
        update=IntegrityError("UPDATE", {}, original),
    )
    db = FakeDb()

    with (
        patch("core.routers.identity_api._identity_repository", return_value=repository),
        patch("core.routers.identity_api.validate_domain_value"),
        pytest.raises(HTTPException) as raised,
    ):
        identity_router.update_master_profile(
            PROFILE_ID,
            MasterProfileUpdate(communication_preferences={"email_opt_in": True}),
            db,
        )

    assert raised.value.status_code == 422
    assert raised.value.detail == {
        "code": "required_profile_field",
        "field": "domain",
        "message": "Domain is required and must be a valid profile domain.",
    }
    assert db.rolled_back is True
