"""Unit tests for the /ad-personalization/settings endpoints
(core.routers.ad_personalization_api).

The real AdPersonalizationRepository is replaced with an in-memory fake, so
these tests cover HTTP wiring only (status codes, schemas, roles, tenant
handling) with no PostgreSQL needed, matching the other router tests. The fake
reuses the real repository's validation rules, so the 422 cases exercise the
real rule code. SQL behaviour is covered by test_ad_personalization_repository.py.
"""

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.ads_database import get_ads_db
from core.repositories.ad_personalization_repository import (
    AdPersonalizationRepository,
    AdPersonalizationValidationError,
    AdTenantNotFoundError,
)
from core.routers.ad_personalization_api import router

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
UNMAPPED = "33333333-3333-3333-3333-333333333333"
USER_ID = "44444444-4444-4444-4444-444444444444"


class FakeAdPersonalizationRepository:
    """In-memory stand-in, keyed by ad tenant id (like the real tables)."""

    tenants: dict[str, int] = {}
    rows: dict[int, dict[tuple[str, str], dict[str, Any]]] = {}

    @classmethod
    def reset(cls) -> None:
        cls.tenants = {TENANT_A: 1, TENANT_B: 2}
        cls.rows = {1: {}, 2: {}}

    def __init__(self, session) -> None:
        self.session = session

    def resolve_ad_tenant_id(self, platform_tenant_id) -> int:
        try:
            return self.tenants[str(platform_tenant_id)]
        except KeyError:
            raise AdTenantNotFoundError("This tenant has no linked ad tenant")

    def list_settings(self, ad_tenant_id: int) -> list[dict[str, Any]]:
        return [row for _key, row in sorted(self.rows[ad_tenant_id].items())]

    def upsert_setting(
        self,
        ad_tenant_id: int,
        *,
        scope_type,
        scope_ref="",
        enabled=None,
        holdout_pct=None,
        consent_mode=None,
        new_measurement_period=False,
        updated_by=None,
    ) -> dict[str, Any]:
        AdPersonalizationRepository._validate(
            scope_type, scope_ref, holdout_pct, consent_mode, new_measurement_period
        )
        tenant_rows = self.rows[ad_tenant_id]
        if scope_type != "tenant" and ("tenant", "") not in tenant_rows:
            raise AdPersonalizationValidationError(
                "Create the tenant-scope settings before a site or placement"
            )
        row = tenant_rows.get(
            (scope_type, scope_ref),
            {
                "scope_type": scope_type,
                "scope_ref": scope_ref,
                "enabled": False,
                "holdout_pct": 10.0,
                "holdout_salt": "default",
                "consent_mode": "opt_in",
                "updated_by": None,
                "updated_at": None,
            },
        )
        if enabled is not None:
            row["enabled"] = enabled
        if holdout_pct is not None:
            row["holdout_pct"] = float(holdout_pct)
        if consent_mode is not None:
            row["consent_mode"] = consent_mode
        if new_measurement_period:
            row["holdout_salt"] = f"period-{uuid.uuid4().hex[:6]}"
        if updated_by is not None:
            row["updated_by"] = updated_by
        stored = dict(row)
        if scope_type != "tenant":
            stored["consent_mode"] = None  # only meaningful on the tenant scope
        tenant_rows[(scope_type, scope_ref)] = row
        return stored


def _build_test_app(claims: dict[str, Any] | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.tenant_id = request.headers.get("X-Tenant-Id", TENANT_A)
        request.state.user_id = request.headers.get("X-User-Id", USER_ID)
        if claims is not None:
            request.state.user = claims
        elif request.headers.get("X-Anonymous") != "1":
            roles = request.headers.get("X-Roles", "tenant_admin")
            request.state.user = {"roles": [r.strip() for r in roles.split(",") if r.strip()]}
        return await call_next(request)

    app.dependency_overrides[get_ads_db] = lambda: None
    return app


@pytest.fixture
def client():
    FakeAdPersonalizationRepository.reset()
    with patch(
        "core.routers.ad_personalization_api.AdPersonalizationRepository",
        FakeAdPersonalizationRepository,
    ):
        yield TestClient(_build_test_app())


def _put(client, **body):
    headers = body.pop("headers", None)
    return client.put("/ad-personalization/settings", json=body, headers=headers)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_the_list_is_empty_before_anything_is_configured(client):
    response = client.get("/ad-personalization/settings")
    assert response.status_code == 200
    assert response.json() == {"settings": []}


def test_the_list_shows_the_callers_settings(client):
    _put(client, scope_type="tenant", enabled=True, holdout_pct=15, consent_mode="opt_out")
    _put(client, scope_type="placement", scope_ref="homepage_top", enabled=False)

    settings = client.get("/ad-personalization/settings").json()["settings"]

    assert [(s["scope_type"], s["scope_ref"]) for s in settings] == [
        ("placement", "homepage_top"),
        ("tenant", ""),
    ]
    tenant = next(s for s in settings if s["scope_type"] == "tenant")
    assert (tenant["enabled"], tenant["holdout_pct"], tenant["consent_mode"]) == (True, 15.0, "opt_out")


def test_a_non_admin_can_read_settings(client):
    with patch("core.auth.SSO_LOGIN", True):
        response = client.get("/ad-personalization/settings", headers={"X-Roles": "analyst"})
    assert response.status_code == 200


def test_an_unmapped_tenant_gets_404_on_read_and_write(client):
    headers = {"X-Tenant-Id": UNMAPPED}
    assert client.get("/ad-personalization/settings", headers=headers).status_code == 404
    assert _put(client, scope_type="tenant", enabled=True, headers=headers).status_code == 404


def test_a_malformed_tenant_header_is_a_400(client):
    response = client.get("/ad-personalization/settings", headers={"X-Tenant-Id": "not-a-uuid"})
    assert response.status_code == 400


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_a_tenant_admin_can_create_the_tenant_settings(client):
    response = _put(client, scope_type="tenant", enabled=True, holdout_pct=10, consent_mode="opt_in")

    assert response.status_code == 200
    body = response.json()
    assert body["scope_type"] == "tenant"
    assert body["scope_ref"] == ""
    assert body["enabled"] is True
    assert body["holdout_pct"] == 10.0
    assert body["consent_mode"] == "opt_in"
    assert body["updated_by"] == USER_ID


def test_consent_mode_is_only_reported_on_the_tenant_scope(client):
    _put(client, scope_type="tenant", enabled=True)
    response = _put(client, scope_type="placement", scope_ref="sidebar", enabled=False)
    assert response.status_code == 200
    assert response.json()["consent_mode"] is None


def test_an_update_only_changes_the_fields_sent(client):
    _put(client, scope_type="tenant", enabled=True, holdout_pct=20)
    response = _put(client, scope_type="tenant", consent_mode="opt_out")

    body = response.json()
    assert body["enabled"] is True
    assert body["holdout_pct"] == 20.0
    assert body["consent_mode"] == "opt_out"


def test_a_new_measurement_period_replaces_the_salt(client):
    first = _put(client, scope_type="tenant", enabled=True).json()
    second = _put(client, scope_type="tenant", new_measurement_period=True).json()
    assert second["holdout_salt"] != first["holdout_salt"]


def test_the_actor_is_taken_from_the_token_claims_when_present():
    FakeAdPersonalizationRepository.reset()
    app = _build_test_app(claims={"roles": ["tenant_admin"], "email": "admin@example.com"})

    with patch(
        "core.routers.ad_personalization_api.AdPersonalizationRepository",
        FakeAdPersonalizationRepository,
    ):
        body = TestClient(app).put(
            "/ad-personalization/settings", json={"scope_type": "tenant", "enabled": True}
        ).json()
    assert body["updated_by"] == "admin@example.com"


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def test_writing_needs_a_tenant_admin_when_sso_is_on(client):
    with patch("core.auth.SSO_LOGIN", True):
        response = _put(client, scope_type="tenant", enabled=True, headers={"X-Roles": "analyst"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Tenant admin role required for ad personalisation settings"


def test_writing_needs_authentication_when_sso_is_on(client):
    with patch("core.auth.SSO_LOGIN", True):
        response = _put(client, scope_type="tenant", enabled=True, headers={"X-Anonymous": "1"})
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["tenant_admin", "admin", "platform_admin"])
def test_the_admin_roles_may_write_when_sso_is_on(client, role):
    with patch("core.auth.SSO_LOGIN", True):
        response = _put(client, scope_type="tenant", enabled=True, headers={"X-Roles": role})
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Validation (422)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"scope_type": "tenant", "holdout_pct": 101},
        {"scope_type": "tenant", "holdout_pct": -1},
        {"scope_type": "tenant", "consent_mode": "maybe"},
        {"scope_type": "galaxy"},
        {"enabled": True},  # scope_type missing
    ],
)
def test_malformed_bodies_are_422(client, body):
    assert _put(client, **body).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"scope_type": "tenant", "scope_ref": "x"},
        {"scope_type": "site"},  # a site needs a key
        {"scope_type": "placement", "scope_ref": "p", "consent_mode": "opt_out"},
        {"scope_type": "placement", "scope_ref": "p", "new_measurement_period": True},
    ],
)
def test_rule_violations_are_422(client, body):
    _put(client, scope_type="tenant", enabled=True)
    assert _put(client, **body).status_code == 422


def test_a_site_or_placement_row_needs_the_tenant_row_first(client):
    response = _put(client, scope_type="placement", scope_ref="homepage_top", enabled=True)
    assert response.status_code == 422
    assert "tenant-scope" in response.json()["detail"]


# --------------------------------------------------------------------------
# Tenant handling
# --------------------------------------------------------------------------


def test_a_tenant_in_the_body_is_ignored(client):
    """The tenant comes from the token. Asking for another one changes nothing."""
    response = _put(client, scope_type="tenant", enabled=True, tenant=TENANT_B)

    assert response.status_code == 200
    assert FakeAdPersonalizationRepository.rows[1]  # the caller's own tenant (A)
    assert not FakeAdPersonalizationRepository.rows[2]  # B untouched


def test_tenants_never_see_each_others_settings(client):
    _put(client, scope_type="tenant", enabled=True, holdout_pct=30)
    _put(client, scope_type="tenant", enabled=False, holdout_pct=5, headers={"X-Tenant-Id": TENANT_B})

    a = client.get("/ad-personalization/settings").json()["settings"]
    b = client.get("/ad-personalization/settings", headers={"X-Tenant-Id": TENANT_B}).json()["settings"]

    assert [s["holdout_pct"] for s in a] == [30.0]
    assert [s["holdout_pct"] for s in b] == [5.0]
