"""Unit tests for Keycloak login -> sys_user/sys_userinfo provisioning
(core.auth._get_or_create_user_on_login / _resolve_tenant_and_user).

Covers the "first login creates sys_user + sys_userinfo rows, subsequent logins 
just refresh last_login_at" behavior, the fail-closed rule when a brand-new
identity's token carries no tenant_id claim, and the Redis identity cache.
"""

import unittest
from unittest.mock import patch

from core.auth import _get_or_create_user_on_login, _resolve_tenant_and_user
from tests.conftest import FakeDBSession, FakeQueryResult


class GetOrCreateUserOnLoginTests(unittest.TestCase):
    def test_new_user_is_created_when_tenant_claim_present(self):
        payload = {
            "sub": "kc-new-user-1",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "preferred_username": "alice",
            "email": "alice@example.com",
            "name": "Alice Anderson",
        }
        session = FakeDBSession(
            script=[
                FakeQueryResult(None),  # SELECT ... FROM sys_userinfo -> not found
                FakeQueryResult({"user_id": "new-user-1", "tenant_id": "11111111-1111-1111-1111-111111111111"}),  # INSERT into sys_user
                FakeQueryResult(None),  # INSERT into sys_userinfo
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            result = _get_or_create_user_on_login(payload)

        self.assertEqual(
            result, {"user_id": "new-user-1", "tenant_id": "11111111-1111-1111-1111-111111111111"}
        )
        self.assertTrue(session.committed)
        self.assertFalse(session.rolled_back)
        # First call = SELECT lookup, second = INSERT sys_user, third = INSERT sys_userinfo
        self.assertEqual(len(session.executed), 3)
        select_sql, select_params = session.executed[0]
        insert_user_sql, insert_user_params = session.executed[1]
        insert_userinfo_sql, insert_userinfo_params = session.executed[2]
        self.assertIn("SELECT", select_sql)
        self.assertIn("sys_userinfo", select_sql)
        self.assertIn("INSERT INTO", insert_user_sql)
        self.assertIn("sys_user", insert_user_sql)
        self.assertIn("INSERT INTO", insert_userinfo_sql)
        self.assertIn("sys_userinfo", insert_userinfo_sql)
        self.assertEqual(insert_user_params["tenant_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(insert_user_params["username"], "alice")
        self.assertEqual(insert_user_params["email"], "alice@example.com")
        self.assertEqual(insert_user_params["full_name"], "Alice Anderson")
        self.assertEqual(insert_userinfo_params["provider_subject_id"], "kc-new-user-1")

    def test_new_user_without_tenant_claim_is_refused_fail_closed(self):
        payload = {"sub": "kc-new-user-2"}  # no tenant_id claim at all

        # With the new fail-closed design, we don't even query the DB if tenant_id is missing.
        # No SessionLocal should be instantiated at all.
        with patch("core.database.SessionLocal") as mock_session_local:
            result = _get_or_create_user_on_login(payload)

        self.assertIsNone(result)
        # No database access should happen without a tenant_id claim
        mock_session_local.assert_not_called()

    def test_existing_user_last_login_is_refreshed_not_recreated(self):
        payload = {
            "sub": "kc-existing-1",
            "tenant_id": "22222222-2222-2222-2222-222222222222",
        }
        session = FakeDBSession(
            script=[
                FakeQueryResult({"user_id": "existing-user-1", "tenant_id": "22222222-2222-2222-2222-222222222222"})
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            result = _get_or_create_user_on_login(payload)

        self.assertEqual(
            result, {"user_id": "existing-user-1", "tenant_id": "22222222-2222-2222-2222-222222222222"}
        )
        self.assertTrue(session.committed)
        # SELECT, UPDATE sys_user, UPDATE sys_userinfo
        self.assertEqual(len(session.executed), 3)
        select_sql, _ = session.executed[0]
        update_user_sql, update_user_params = session.executed[1]
        update_userinfo_sql, _ = session.executed[2]
        self.assertIn("SELECT", select_sql)
        self.assertIn("UPDATE", update_user_sql)
        self.assertIn("sys_user", update_user_sql)
        self.assertIn("last_login_at", update_user_sql)
        self.assertIn("UPDATE", update_userinfo_sql)
        self.assertIn("sys_userinfo", update_userinfo_sql)
        self.assertEqual(update_user_params["uid"], "existing-user-1")
        # Must never attempt to (re-)insert an existing user.
        for sql, _ in session.executed:
            self.assertNotIn("INSERT INTO sys_user", sql)

    def test_missing_sub_and_tenant_claim_returns_none_without_touching_db(self):
        with patch("core.database.SessionLocal") as mock_session_local:
            result = _get_or_create_user_on_login({"preferred_username": "no-sub-or-tenant"})

        self.assertIsNone(result)
        mock_session_local.assert_not_called()

    def test_database_error_rolls_back_and_returns_none(self):
        payload = {
            "sub": "kc-broken",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
        }
        session = FakeDBSession(raise_on_call=1)  # SELECT itself raises

        with patch("core.database.SessionLocal", return_value=session):
            result = _get_or_create_user_on_login(payload)

        self.assertIsNone(result)
        self.assertTrue(session.rolled_back)
        self.assertTrue(session.closed)

    def test_session_is_always_closed(self):
        payload = {
            "sub": "kc-existing-2",
            "tenant_id": "22222222-2222-2222-2222-222222222222",
        }
        session = FakeDBSession(
            script=[FakeQueryResult({"user_id": "u", "tenant_id": "t"})]
        )

        with patch("core.database.SessionLocal", return_value=session):
            _get_or_create_user_on_login(payload)

        self.assertTrue(session.closed)


class SysUserInfoProviderScopingTests(unittest.TestCase):
    """Covers sys_userinfo-specific semantics: the lookup/insert must always
    scope to auth_provider='KEYCLOAK' (a user can hold one row per provider,
    per uq_sys_userinfo_user_provider) and tenant_id (per
    uq_sys_userinfo_provider_id), never leaking across providers or tenants."""

    def test_lookup_query_scopes_to_keycloak_provider_and_tenant(self):
        payload = {"sub": "kc-scope-1", "tenant_id": "tenant-scope"}
        session = FakeDBSession(
            script=[
                FakeQueryResult({"user_id": "u-scope", "tenant_id": "tenant-scope"}),
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            _get_or_create_user_on_login(payload)

        select_sql, select_params = session.executed[0]
        self.assertIn("sys_userinfo", select_sql)
        self.assertIn("KEYCLOAK", select_sql)
        self.assertEqual(select_params["tenant_id"], "tenant-scope")
        self.assertEqual(select_params["provider_subject_id"], "kc-scope-1")

    def test_new_sys_userinfo_row_links_keycloak_provider_to_new_user(self):
        payload = {"sub": "kc-link-1", "tenant_id": "tenant-link"}
        session = FakeDBSession(
            script=[
                FakeQueryResult(None),  # no existing sys_userinfo/sys_user
                FakeQueryResult({"user_id": "u-link", "tenant_id": "tenant-link"}),
                FakeQueryResult(None),
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            result = _get_or_create_user_on_login(payload)

        self.assertEqual(result, {"user_id": "u-link", "tenant_id": "tenant-link"})
        insert_userinfo_sql, insert_userinfo_params = session.executed[2]
        self.assertIn("KEYCLOAK", insert_userinfo_sql)
        self.assertEqual(insert_userinfo_params["tenant_id"], "tenant-link")
        self.assertEqual(insert_userinfo_params["user_id"], "u-link")
        self.assertEqual(insert_userinfo_params["provider_subject_id"], "kc-link-1")

    def test_same_subject_id_in_different_tenant_is_treated_as_a_new_identity(self):
        """uq_sys_userinfo_provider_id is scoped per-tenant, so the same
        Keycloak subject logging into a different tenant must never resolve
        to another tenant's existing row -- it provisions fresh."""
        payload = {"sub": "kc-shared-subject", "tenant_id": "tenant-other"}
        session = FakeDBSession(
            script=[
                FakeQueryResult(None),  # not found for this tenant, even though it exists for tenant-original
                FakeQueryResult({"user_id": "u-other-tenant", "tenant_id": "tenant-other"}),
                FakeQueryResult(None),
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            result = _get_or_create_user_on_login(payload)

        self.assertEqual(result, {"user_id": "u-other-tenant", "tenant_id": "tenant-other"})
        select_sql, select_params = session.executed[0]
        self.assertEqual(select_params["tenant_id"], "tenant-other")

    def test_username_falls_back_to_email_when_preferred_username_missing(self):
        payload = {
            "sub": "kc-fallback-1",
            "tenant_id": "tenant-fb",
            "email": "fallback@example.com",
        }
        session = FakeDBSession(
            script=[
                FakeQueryResult(None),
                FakeQueryResult({"user_id": "u-fb1", "tenant_id": "tenant-fb"}),
                FakeQueryResult(None),
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            _get_or_create_user_on_login(payload)

        _, insert_user_params = session.executed[1]
        self.assertEqual(insert_user_params["username"], "fallback@example.com")

    def test_username_falls_back_to_provider_subject_id_when_no_username_or_email(self):
        payload = {"sub": "kc-fallback-2", "tenant_id": "tenant-fb"}
        session = FakeDBSession(
            script=[
                FakeQueryResult(None),
                FakeQueryResult({"user_id": "u-fb2", "tenant_id": "tenant-fb"}),
                FakeQueryResult(None),
            ]
        )

        with patch("core.database.SessionLocal", return_value=session):
            _get_or_create_user_on_login(payload)

        _, insert_user_params = session.executed[1]
        self.assertEqual(insert_user_params["username"], "kc-fallback-2")
        self.assertIsNone(insert_user_params["email"])
        self.assertIsNone(insert_user_params["full_name"])


class ResolveTenantAndUserTests(unittest.TestCase):
    def test_explicit_token_claims_short_circuit_db_and_cache(self):
        payload = {
            "sub": "kc-x",
            "tenant_id": "tenant-from-claim",
            "user_id": "user-from-claim",
        }

        with patch("core.auth._load_cached_identity", side_effect=AssertionError("should not check cache")), patch(
            "core.auth._get_or_create_user_on_login", side_effect=AssertionError("should not hit DB")
        ):
            tenant_id, user_id = _resolve_tenant_and_user(payload)

        self.assertEqual((tenant_id, user_id), ("tenant-from-claim", "user-from-claim"))

    def test_missing_sub_and_claims_returns_none_none(self):
        tenant_id, user_id = _resolve_tenant_and_user({"preferred_username": "no-identity"})
        self.assertIsNone(tenant_id)
        self.assertIsNone(user_id)

    def test_tenant_claim_present_but_missing_sub_returns_none_none(self):
        """A tenant_id claim alone can't resolve an identity -- sub is required
        to look up (or provision) the sys_userinfo row."""
        tenant_id, user_id = _resolve_tenant_and_user({"tenant_id": "tenant-only"})
        self.assertIsNone(tenant_id)
        self.assertIsNone(user_id)

    def test_cache_hit_avoids_db_lookup(self):
        payload = {
            "sub": "kc-cached-1",
            "tenant_id": "tenant-cached",
        }

        with patch(
            "core.auth._load_cached_identity",
            return_value={"tenant_id": "tenant-cached", "user_id": "user-cached"},
        ), patch("core.auth._get_or_create_user_on_login", side_effect=AssertionError("must not hit DB on cache hit")):
            tenant_id, user_id = _resolve_tenant_and_user(payload)

        self.assertEqual((tenant_id, user_id), ("tenant-cached", "user-cached"))

    def test_cache_miss_resolves_via_db_and_populates_cache(self):
        payload = {
            "sub": "kc-cache-miss-1",
            "tenant_id": "t-1",
        }

        with patch("core.auth._load_cached_identity", return_value=None), patch(
            "core.auth._get_or_create_user_on_login",
            return_value={"user_id": "u-1", "tenant_id": "t-1"},
        ), patch("core.auth._cache_identity") as mock_cache_identity:
            tenant_id, user_id = _resolve_tenant_and_user(payload)

        self.assertEqual((tenant_id, user_id), ("t-1", "u-1"))
        # Cache identity is now called with (provider_subject_id, tenant_id, identity)
        mock_cache_identity.assert_called_once_with("kc-cache-miss-1", "t-1", {"user_id": "u-1", "tenant_id": "t-1"})

    def test_failed_provisioning_returns_none_none_and_does_not_cache(self):
        payload = {
            "sub": "kc-cannot-provision",
            "tenant_id": "t-1",
        }

        with patch("core.auth._load_cached_identity", return_value=None), patch(
            "core.auth._get_or_create_user_on_login", return_value=None
        ), patch("core.auth._cache_identity") as mock_cache_identity:
            tenant_id, user_id = _resolve_tenant_and_user(payload)

        self.assertIsNone(tenant_id)
        self.assertIsNone(user_id)
        mock_cache_identity.assert_not_called()


class MultiTenantLoginIsolationTests(unittest.TestCase):
    """Two different Keycloak identities logging in must never bleed into
    each other's tenant/user resolution."""

    def test_two_different_users_resolve_to_their_own_distinct_tenants(self):
        sessions = {
            "kc-tenant-a-user": FakeDBSession(
                script=[FakeQueryResult({"user_id": "user-a", "tenant_id": "tenant-a"})]
            ),
            "kc-tenant-b-user": FakeDBSession(
                script=[FakeQueryResult({"user_id": "user-b", "tenant_id": "tenant-b"})]
            ),
        }

        results = {}
        for sub, session in sessions.items():
            tenant_id = "tenant-a" if sub == "kc-tenant-a-user" else "tenant-b"
            with patch("core.database.SessionLocal", return_value=session):
                results[sub] = _get_or_create_user_on_login({
                    "sub": sub,
                    "tenant_id": tenant_id,
                })

        self.assertEqual(results["kc-tenant-a-user"], {"user_id": "user-a", "tenant_id": "tenant-a"})
        self.assertEqual(results["kc-tenant-b-user"], {"user_id": "user-b", "tenant_id": "tenant-b"})
        self.assertNotEqual(results["kc-tenant-a-user"]["tenant_id"], results["kc-tenant-b-user"]["tenant_id"])


if __name__ == "__main__":
    unittest.main()
