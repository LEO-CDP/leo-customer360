"""HTTP tests for tenant system-user role management."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.routers.user_api import (
    get_current_user,
    get_db_session,
    get_tenant_id,
    router,
)


TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _user_response() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": str(USER_ID),
        "username": "managed-user",
        "email": None,
        "full_name": "Managed User",
        "phone": None,
        "job_title": None,
        "department": None,
        "organization_id": None,
        "language_code": "en",
        "timezone": "UTC",
        "status": "ACTIVE",
        "last_login_at": None,
        "created_at": now,
        "updated_at": now,
        "roles": ["Admin"],
        "sso_identities": [],
    }


class FakeUserRepository:
    assigned_roles: list[str] = []

    def __init__(self, db):
        self.db = db

    def get_user_by_id(self, user_id, tenant_id):
        return SimpleNamespace(user_id=user_id, tenant_id=tenant_id, status="ACTIVE")

    def get_role_codes_for_user(self, tenant_id, user_id):
        return list(self.assigned_roles)

    def replace_user_roles(self, tenant_id, user_id, roles):
        self.assigned_roles = list(roles)

    def get_user_by_id_cached(self, user_id, tenant_id):
        response = _user_response()
        response["roles"] = ["Root / Super Admin"] if "root" in self.assigned_roles else ["Admin"]
        return response


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: {"status": "ACTIVE"}
    app.dependency_overrides[get_tenant_id] = lambda: TENANT_ID

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.state.user = {
            "roles": [
                role.strip()
                for role in request.headers.get("X-Roles", "root").split(",")
                if role.strip()
            ]
        }
        request.state.user_id = str(USER_ID)
        request.state.tenant_id = str(TENANT_ID)
        return await call_next(request)

    return app


def test_root_can_list_all_canonical_roles():
    response = TestClient(_build_app()).get("/users/roles", headers={"X-Roles": "root"})

    assert response.status_code == 200
    assert [item["role_code"] for item in response.json()] == [
        "root",
        "admin",
        "operator",
        "developer",
        "auditor",
        "partner",
        "billing",
        "editor",
        "contributor",
        "viewer",
    ]


def test_non_root_admin_cannot_assign_root():
    with patch("core.routers.user_api.UserRepository", FakeUserRepository):
        response = TestClient(_build_app()).put(
            f"/users/{USER_ID}/roles",
            headers={"X-Roles": "admin"},
            json={"roles": ["root"]},
        )

    assert response.status_code == 403
    assert "Only a root user" in response.json()["detail"]


def test_root_can_assign_root_and_admin_roles():
    FakeUserRepository.assigned_roles = []
    with patch("core.routers.user_api.UserRepository", FakeUserRepository):
        response = TestClient(_build_app()).put(
            f"/users/{USER_ID}/roles",
            headers={"X-Roles": "root"},
            json={"roles": ["root", "admin"]},
        )

    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["Root / Super Admin"]
