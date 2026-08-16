"""Unit tests for core.repositories.auth_repository.AuthRepository.

Uses the same FakeDBSession/FakeQueryResult fakes as
tests/test_keycloak_login_provisioning.py, but exercises the repository
methods directly (no core.auth / SessionLocal involved), verifying the exact
SQL text and bound parameters used for each sys_user/sys_userinfo operation.
"""

import unittest
from unittest import mock

from core.repositories.auth_repository import AuthRepository, _validated_schema
from tests.conftest import FakeDBSession, FakeQueryResult


class GetExistingUserForKeycloakLoginTests(unittest.TestCase):
    def test_returns_dict_when_row_found(self):
        session = FakeDBSession(script=[FakeQueryResult({"user_id": "u-1", "tenant_id": "t-1"})])
        repo = AuthRepository(session)

        result = repo.get_existing_user_for_keycloak_login("t-1", "kc-sub-1")

        self.assertEqual(result, {"user_id": "u-1", "tenant_id": "t-1"})
        sql, params = session.executed[0]
        self.assertIn("SELECT", sql)
        self.assertIn("sys_userinfo", sql)
        self.assertIn("sys_user", sql)
        self.assertIn("KEYCLOAK", sql)
        self.assertEqual(params, {"tenant_id": "t-1", "provider_subject_id": "kc-sub-1"})

    def test_returns_none_when_no_row_found(self):
        session = FakeDBSession(script=[FakeQueryResult(None)])
        repo = AuthRepository(session)

        result = repo.get_existing_user_for_keycloak_login("t-1", "kc-sub-missing")

        self.assertIsNone(result)


class RefreshLoginMetadataTests(unittest.TestCase):
    def test_updates_sys_user_and_sys_userinfo_with_expected_params(self):
        session = FakeDBSession(script=[FakeQueryResult(None), FakeQueryResult(None)])
        repo = AuthRepository(session)

        repo.refresh_login_metadata("u-1", "t-1", "kc-sub-1")

        self.assertEqual(len(session.executed), 2)
        update_user_sql, update_user_params = session.executed[0]
        update_userinfo_sql, update_userinfo_params = session.executed[1]

        self.assertIn("UPDATE", update_user_sql)
        self.assertIn("sys_user", update_user_sql)
        self.assertIn("last_login_at", update_user_sql)
        self.assertEqual(update_user_params, {"uid": "u-1"})

        self.assertIn("UPDATE", update_userinfo_sql)
        self.assertIn("sys_userinfo", update_userinfo_sql)
        self.assertIn("KEYCLOAK", update_userinfo_sql)
        self.assertEqual(update_userinfo_params, {"tenant_id": "t-1", "provider_subject_id": "kc-sub-1"})


class ProvisionNewUserForKeycloakLoginTests(unittest.TestCase):
    def test_inserts_sys_user_then_sys_userinfo_and_returns_identity(self):
        session = FakeDBSession(
            script=[
                FakeQueryResult({"user_id": "new-user", "tenant_id": "t-1"}),
                FakeQueryResult(None),
            ]
        )
        repo = AuthRepository(session)
        payload = {"preferred_username": "alice", "email": "alice@example.com", "name": "Alice Anderson"}

        result = repo.provision_new_user_for_keycloak_login("t-1", payload, "kc-sub-1")

        self.assertEqual(result, {"user_id": "new-user", "tenant_id": "t-1"})
        self.assertEqual(len(session.executed), 2)

        insert_user_sql, insert_user_params = session.executed[0]
        insert_userinfo_sql, insert_userinfo_params = session.executed[1]

        self.assertIn("INSERT INTO", insert_user_sql)
        self.assertIn("sys_user", insert_user_sql)
        self.assertEqual(
            insert_user_params,
            {"tenant_id": "t-1", "username": "alice", "email": "alice@example.com", "full_name": "Alice Anderson"},
        )

        self.assertIn("INSERT INTO", insert_userinfo_sql)
        self.assertIn("sys_userinfo", insert_userinfo_sql)
        self.assertIn("KEYCLOAK", insert_userinfo_sql)
        self.assertEqual(
            insert_userinfo_params,
            {"tenant_id": "t-1", "user_id": "new-user", "provider_subject_id": "kc-sub-1"},
        )

    def test_username_falls_back_to_email_then_provider_subject_id(self):
        session = FakeDBSession(
            script=[FakeQueryResult({"user_id": "u", "tenant_id": "t-1"}), FakeQueryResult(None)]
        )
        repo = AuthRepository(session)

        repo.provision_new_user_for_keycloak_login("t-1", {"email": "fallback@example.com"}, "kc-sub-2")

        _, insert_user_params = session.executed[0]
        self.assertEqual(insert_user_params["username"], "fallback@example.com")

        session2 = FakeDBSession(
            script=[FakeQueryResult({"user_id": "u2", "tenant_id": "t-1"}), FakeQueryResult(None)]
        )
        AuthRepository(session2).provision_new_user_for_keycloak_login("t-1", {}, "kc-sub-3")
        _, insert_user_params2 = session2.executed[0]
        self.assertEqual(insert_user_params2["username"], "kc-sub-3")

    def test_returns_none_and_skips_sys_userinfo_insert_when_sys_user_insert_fails(self):
        session = FakeDBSession(script=[FakeQueryResult(None)])
        repo = AuthRepository(session)

        result = repo.provision_new_user_for_keycloak_login("t-1", {"preferred_username": "bob"}, "kc-sub-4")

        self.assertIsNone(result)
        self.assertEqual(len(session.executed), 1)


class GetOrCreateKeycloakUserTests(unittest.TestCase):
    """Covers the cohesive lookup-vs-provision entry point used by core.auth."""

    def test_refreshes_existing_user_without_provisioning(self):
        session = FakeDBSession(
            script=[
                FakeQueryResult({"user_id": "existing", "tenant_id": "t-1"}),  # SELECT
                FakeQueryResult(None),  # UPDATE sys_user
                FakeQueryResult(None),  # UPDATE sys_userinfo
            ]
        )
        repo = AuthRepository(session)

        result = repo.get_or_create_keycloak_user("t-1", {"sub": "kc-existing"}, "kc-existing")

        self.assertEqual(result, {"user_id": "existing", "tenant_id": "t-1"})
        self.assertEqual(len(session.executed), 3)
        for sql, _ in session.executed:
            self.assertNotIn("INSERT INTO", sql)

    def test_provisions_new_user_when_none_exists(self):
        session = FakeDBSession(
            script=[
                FakeQueryResult(None),  # SELECT -> not found
                FakeQueryResult({"user_id": "new-user", "tenant_id": "t-1"}),  # INSERT sys_user
                FakeQueryResult(None),  # INSERT sys_userinfo
            ]
        )
        repo = AuthRepository(session)

        result = repo.get_or_create_keycloak_user("t-1", {"preferred_username": "carl"}, "kc-new")

        self.assertEqual(result, {"user_id": "new-user", "tenant_id": "t-1"})
        self.assertEqual(len(session.executed), 3)
        select_sql, _ = session.executed[0]
        insert_user_sql, _ = session.executed[1]
        self.assertIn("SELECT", select_sql)
        self.assertIn("INSERT INTO", insert_user_sql)


class SchemaValidationTests(unittest.TestCase):
    """Defense-in-depth: db_schema is trusted config, never request input,
    but the repository should still refuse to build queries against a schema
    name that isn't a plain SQL identifier."""

    def test_accepts_plain_identifier(self):
        self.assertEqual(_validated_schema("customer360"), "customer360")
        self.assertEqual(_validated_schema("_private_schema"), "_private_schema")

    def test_rejects_non_identifier_schema(self):
        for bad_schema in ["customer360;drop table sys_user;--", "public schema", "1invalid", ""]:
            with self.assertRaises(ValueError):
                _validated_schema(bad_schema)

    def test_repository_init_raises_on_unsafe_configured_schema(self):
        session = FakeDBSession()
        with mock.patch("core.repositories.auth_repository.settings.db_schema", "bad;schema"):
            with self.assertRaises(ValueError):
                AuthRepository(session)
