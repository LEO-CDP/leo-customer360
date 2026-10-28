"""Unit tests for POST /auth/refresh (core.routers.auth_api.sso_refresh).

This endpoint is what keeps an SSO admin signed in past a single access-token
lifetime: the browser trades its refresh token for a new access token instead
of being bounced back to the Keycloak login page. Keycloak itself is mocked at
the repository boundary, so no realm is required.
"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from core.routers.auth_api import router


def _client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class SsoRefreshTests(unittest.TestCase):
    def test_refresh_returns_a_new_access_token(self):
        keycloak_tokens = {
            "access_token": "new-access-token",
            "refresh_token": "rotated-refresh-token",
            "id_token": "new-id-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

        with patch("leo_customer360_dao.config.settings.sso_login", True), patch(
            "core.repositories.auth_repository.AuthRepository.refresh_tokens",
            return_value=keycloak_tokens,
        ) as mock_refresh:
            response = _client().post("/auth/refresh", json={"refresh_token": "old-refresh-token"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["access_token"], "new-access-token")
        # The rotated refresh token must be handed back, or the NEXT refresh
        # would replay a token Keycloak has already retired.
        self.assertEqual(body["refresh_token"], "rotated-refresh-token")
        mock_refresh.assert_called_once_with("old-refresh-token")

    def test_expires_in_is_clamped_to_what_the_middleware_accepts(self):
        """Keycloak may issue a longer-lived token than this API honours; the
        browser must schedule its renewal against the API's limit, not the
        realm's, or it renews after the API has already started 401-ing."""
        with patch("leo_customer360_dao.config.settings.sso_login", True), patch(
            "leo_customer360_dao.config.settings.keycloak_token_expires_minutes", 60
        ), patch(
            "core.repositories.auth_repository.AuthRepository.refresh_tokens",
            return_value={"access_token": "t", "expires_in": 86400},
        ):
            response = _client().post("/auth/refresh", json={"refresh_token": "rt"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["expires_in"], 3600)

    def test_expired_refresh_token_is_rejected_with_401(self):
        with patch("leo_customer360_dao.config.settings.sso_login", True), patch(
            "core.repositories.auth_repository.AuthRepository.refresh_tokens",
            side_effect=HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token is expired or invalid",
            ),
        ):
            response = _client().post("/auth/refresh", json={"refresh_token": "long-dead"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Refresh token is expired or invalid")

    def test_refresh_is_refused_when_sso_is_disabled(self):
        with patch("leo_customer360_dao.config.settings.sso_login", False):
            response = _client().post("/auth/refresh", json={"refresh_token": "rt"})

        self.assertEqual(response.status_code, 400)

    def test_missing_refresh_token_is_a_validation_error(self):
        with patch("leo_customer360_dao.config.settings.sso_login", True):
            response = _client().post("/auth/refresh", json={})

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
