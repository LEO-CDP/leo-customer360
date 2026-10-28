"""Regression tests for Keycloak realm bootstrap settings."""

import contextlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "bootstrap-realm.py"

# Cleared before each load so a value in the developer's own shell can never
# decide what the "defaults" test observes.
LIFETIME_VARS = (
    "KEYCLOAK_TOKEN_EXPIRES_MINUTES",
    "KEYCLOAK_SSO_SESSION_IDLE_MINUTES",
    "KEYCLOAK_SSO_SESSION_MAX_MINUTES",
)


@contextlib.contextmanager
def loaded_script(extra_env=None):
    """Import bootstrap-realm.py under a controlled environment.

    The script reads its lifetimes at import time, so the environment has to
    be in place before exec_module -- and stay in place for the main() call.
    """
    # A directory, not NamedTemporaryFile: main() reopens ENV_FILE to write the
    # client secret, and Windows refuses a second open on a still-open temp file.
    with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
        os.environ,
        {
            "KC_URL": "http://keycloak.test",
            "KEYCLOAK_ADMIN_PASSWORD": "admin-password",
            "KC_TEST_USER_PASSWORD": "test-password",
            "REDIRECT_URIS": "https://app.example.com/callback",
            "ENV_FILE": os.path.join(tmp_dir, ".env"),
            **(extra_env or {}),
        },
        clear=False,
    ):
        for name in LIFETIME_VARS:
            if name not in (extra_env or {}):
                os.environ.pop(name, None)
        spec = importlib.util.spec_from_file_location("bootstrap_realm_test", SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        yield module


def run_with_fake_keycloak(module):
    """Drive module.main() against a stubbed Keycloak and return its calls."""
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
    return calls


def realm_update(calls, module):
    updates = [b for m, p, b in calls if m == "PUT" and p == f"/admin/realms/{module.REALM}"]
    assert len(updates) == 1
    return updates[0]


def client_update(calls):
    updates = [b for m, p, b in calls if m == "PUT" and p.endswith("/clients/client-id")]
    assert len(updates) == 1
    return updates[0]


class BootstrapRealmTests(unittest.TestCase):
    def test_existing_realm_preserves_settings_and_applies_default_lifetimes(self):
        with loaded_script() as module:
            calls = run_with_fake_keycloak(module)

        realm = realm_update(calls, module)
        self.assertTrue(realm["bruteForceProtected"])
        self.assertEqual(realm["loginTheme"], "company-theme")
        # 60m token inside an 8h idle / 10h max session.
        self.assertEqual(realm["accessTokenLifespan"], 3600)
        self.assertEqual(realm["ssoSessionIdleTimeout"], 28800)
        self.assertEqual(realm["ssoSessionMaxLifespan"], 36000)

        client = client_update(calls)
        self.assertEqual(client["attributes"]["custom"], "keep")
        self.assertEqual(client["attributes"]["access.token.lifespan"], "3600")

    def test_session_outlives_a_single_access_token(self):
        """The bug this guards: all three lifetimes equal meant the SSO session
        died with the first access token, so no refresh could keep anyone in."""
        with loaded_script() as module:
            calls = run_with_fake_keycloak(module)

        realm = realm_update(calls, module)
        self.assertGreater(realm["ssoSessionIdleTimeout"], realm["accessTokenLifespan"])
        self.assertGreater(realm["ssoSessionMaxLifespan"], realm["accessTokenLifespan"])

    def test_lifetimes_are_configurable_from_the_environment(self):
        with loaded_script(
            {
                "KEYCLOAK_TOKEN_EXPIRES_MINUTES": "90",
                "KEYCLOAK_SSO_SESSION_IDLE_MINUTES": "240",
                "KEYCLOAK_SSO_SESSION_MAX_MINUTES": "300",
            }
        ) as module:
            calls = run_with_fake_keycloak(module)

        realm = realm_update(calls, module)
        self.assertEqual(realm["accessTokenLifespan"], 5400)
        self.assertEqual(realm["ssoSessionIdleTimeout"], 14400)
        self.assertEqual(realm["ssoSessionMaxLifespan"], 18000)
        self.assertEqual(client_update(calls)["attributes"]["client.session.idle.timeout"], "14400")

    def test_session_shorter_than_token_lifetime_is_refused(self):
        with self.assertRaises(SystemExit):
            with loaded_script(
                {
                    "KEYCLOAK_TOKEN_EXPIRES_MINUTES": "60",
                    "KEYCLOAK_SSO_SESSION_IDLE_MINUTES": "30",
                    "KEYCLOAK_SSO_SESSION_MAX_MINUTES": "600",
                }
            ):
                pass

    def test_non_numeric_lifetime_is_refused(self):
        with self.assertRaises(SystemExit):
            with loaded_script({"KEYCLOAK_TOKEN_EXPIRES_MINUTES": "forever"}):
                pass


if __name__ == "__main__":
    unittest.main()
