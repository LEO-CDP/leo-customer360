"""Regression tests for Keycloak realm bootstrap settings."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "bootstrap-realm.py"


class BootstrapRealmTests(unittest.TestCase):
    def test_existing_realm_preserves_settings_and_sets_30_minute_lifetimes(self):
        with tempfile.NamedTemporaryFile() as env_file, patch.dict(
            os.environ,
            {
                "KC_URL": "http://keycloak.test",
                "KEYCLOAK_ADMIN_PASSWORD": "admin-password",
                "KC_TEST_USER_PASSWORD": "test-password",
                "REDIRECT_URIS": "https://app.example.com/callback",
                "ENV_FILE": env_file.name,
            },
            clear=False,
        ):
            spec = importlib.util.spec_from_file_location("bootstrap_realm_test", SCRIPT_PATH)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            existing_realm = {
                "realm": module.REALM,
                "enabled": True,
                "bruteForceProtected": True,
                "loginTheme": "company-theme",
            }
            calls = []

            def fake_req(method, path, token=None, body=None, form=None):
                calls.append((method, path, body))
                if path.endswith("/protocol/openid-connect/token"):
                    return 200, {"access_token": "admin-token"}, ""
                if path == f"/admin/realms/{module.REALM}":
                    if method == "GET":
                        return 200, existing_realm, ""
                    return 204, {}, ""
                if "/roles/" in path:
                    return 200, {"id": "role-id", "name": "platform_admin"}, ""
                if path.endswith("/users/profile"):
                    return 200, {"unmanagedAttributePolicy": "ENABLED"}, ""
                if path.endswith("/clients?clientId=customer360-api"):
                    return 200, [{"id": "client-id", "attributes": {"custom": "keep"}}], ""
                if path.endswith("/protocol-mappers/models"):
                    return 200, [
                        {"name": "tenant_id"},
                        {"name": "user_id"},
                        {"name": f"aud-{module.CLIENT_ID}"},
                    ], ""
                if path.endswith("/client-secret"):
                    return 200, {"value": "client-secret"}, ""
                if "/users?username=" in path:
                    return 200, [{"id": "user-id"}], ""
                return 204, {}, ""

            module.req = fake_req
            module.main()

        realm_updates = [body for method, path, body in calls if method == "PUT" and path == f"/admin/realms/{module.REALM}"]
        self.assertEqual(len(realm_updates), 1)
        self.assertTrue(realm_updates[0]["bruteForceProtected"])
        self.assertEqual(realm_updates[0]["loginTheme"], "company-theme")
        self.assertEqual(realm_updates[0]["accessTokenLifespan"], 1800)
        self.assertEqual(realm_updates[0]["ssoSessionIdleTimeout"], 1800)
        self.assertEqual(realm_updates[0]["ssoSessionMaxLifespan"], 1800)

        client_updates = [body for method, path, body in calls if method == "PUT" and path.endswith("/clients/client-id")]
        self.assertEqual(len(client_updates), 1)
        self.assertEqual(client_updates[0]["attributes"]["custom"], "keep")
        self.assertEqual(client_updates[0]["attributes"]["access.token.lifespan"], "1800")


if __name__ == "__main__":
    unittest.main()