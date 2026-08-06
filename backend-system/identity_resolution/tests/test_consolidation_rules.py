"""Unit tests for merge precedence driven by cdp_profile_attributes.

These tests focus on consolidation_rule / consolidation_config behavior so the
resolver can prioritize recent or verified data without hard-coded merge logic.
"""

from datetime import datetime, timezone

from identity_resolution.models import IdentityRule
from identity_resolution.resolver import CustomerIdentityResolver


def make_resolver(mock_conn, **kwargs):
    return CustomerIdentityResolver(mock_conn, schema="customer360", **kwargs)


class TestScalarConsolidationHelpers:
    def test_most_recent_prefers_newer_value(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {"updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "full_name",
            "exact",
            consolidation_rule="most_recent",
            consolidation_config={"timestamp_field": "updated_at"},
        )

        result = resolver._resolve_scalar_consolidation(
            "full_name",
            "Old Name",
            "New Name",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "New Name"

    def test_most_recent_keeps_existing_when_new_value_is_older(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {"updated_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        raw_profile = {"created_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "full_name",
            "exact",
            consolidation_rule="most_recent",
            consolidation_config={"timestamp_field": "updated_at"},
        )

        result = resolver._resolve_scalar_consolidation(
            "full_name",
            "Old Name",
            "New Name",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "Old Name"

    def test_verified_first_prefers_verified_existing_value(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {"kyc_status": "verified", "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "email",
            "exact",
            consolidation_rule="verified_first",
            consolidation_config={
                "verified_field": "kyc_status",
                "verified_values": ["verified"],
                "fallback_mode": "most_recent",
                "timestamp_field": "updated_at",
            },
        )

        result = resolver._resolve_scalar_consolidation(
            "email",
            "old@example.com",
            "new@example.com",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "old@example.com"

    def test_verified_first_uses_incoming_verified_value(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {"kyc_status": "pending", "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"kyc_status": "verified", "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "phone_number",
            "exact",
            consolidation_rule="verified_first",
            consolidation_config={
                "verified_field": "kyc_status",
                "verified_values": ["verified"],
                "fallback_mode": "most_recent",
                "timestamp_field": "updated_at",
            },
        )

        result = resolver._resolve_scalar_consolidation(
            "phone_number",
            "old-phone",
            "new-phone",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "new-phone"

    def test_verified_then_most_recent_falls_back_to_recency(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {"kyc_status": "pending", "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"kyc_status": "pending", "created_at": datetime(2026, 7, 3, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "national_id",
            "exact",
            consolidation_rule="verified_then_most_recent",
            consolidation_config={
                "verified_field": "kyc_status",
                "verified_values": ["verified"],
                "timestamp_field": "updated_at",
            },
        )

        result = resolver._resolve_scalar_consolidation(
            "national_id",
            "old-id",
            "new-id",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "new-id"

    def test_source_priority_prefers_higher_ranked_source(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {"source_systems": ["core_banking"], "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"source_system": "moengage", "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "external_customer_id",
            "exact",
            consolidation_rule="source_priority",
            consolidation_config={"source_priority": ["core_banking", "crm", "moengage"]},
        )

        result = resolver._resolve_scalar_consolidation(
            "external_customer_id",
            "old-external-id",
            "new-external-id",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "old-external-id"

    def test_non_null_keeps_existing_value(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {}
        raw_profile = {"created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule("full_name", "exact", consolidation_rule="non_null")

        result = resolver._resolve_scalar_consolidation(
            "full_name",
            "current name",
            "incoming name",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "current name"

    def test_overwrite_uses_incoming_value(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {}
        raw_profile = {"created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule("full_name", "exact", consolidation_rule="overwrite")

        result = resolver._resolve_scalar_consolidation(
            "full_name",
            "current name",
            "incoming name",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "incoming name"

    def test_append_distinct_merges_lists_without_duplicates(self, mock_conn):
        resolver = make_resolver(mock_conn)
        current_master = {}
        raw_profile = {}
        rule = IdentityRule("source_systems", "exact", consolidation_rule="append_distinct")

        result = resolver._resolve_scalar_consolidation(
            "source_systems",
            ["web", "crm"],
            ["crm", "mobile"],
            rule,
            raw_profile,
            current_master,
        )

        assert result == ["web", "crm", "mobile"]

    def test_verified_first_uses_verified_event_name_as_proof(self, mock_conn):
        """cdp_raw_profiles_stage has no kyc_status column -- verified_event_names
        lets a specific business event (e.g. 'kyc-completed') on the incoming
        raw profile itself count as verification proof."""
        resolver = make_resolver(mock_conn)
        current_master = {"kyc_status": "pending", "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"event_name": "kyc-completed", "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "national_id",
            "exact",
            consolidation_rule="verified_first",
            consolidation_config={
                "verified_field": "kyc_status",
                "verified_values": ["verified"],
                "verified_event_names": ["kyc-completed"],
                "fallback_mode": "most_recent",
                "timestamp_field": "updated_at",
            },
        )

        result = resolver._resolve_scalar_consolidation(
            "national_id",
            "old-id",
            "new-id",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "new-id"

    def test_source_priority_matching_is_case_insensitive(self, mock_conn):
        """Real source_system values are PascalCase (e.g. 'AppsFlyer'); config
        authors may not match casing exactly -- ranking must still work."""
        resolver = make_resolver(mock_conn)
        current_master = {"source_systems": ["MoEngage"], "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"source_system": "CoreBanking", "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "external_customer_id",
            "exact",
            consolidation_rule="source_priority",
            consolidation_config={"source_priority": ["corebanking", "moengage"]},
        )

        result = resolver._resolve_scalar_consolidation(
            "external_customer_id",
            "old-external-id",
            "new-external-id",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "new-external-id"

    def test_most_recent_handles_naive_vs_aware_timestamp_mismatch(self, mock_conn):
        """A naive/aware datetime comparison must never raise -- fall back to
        keeping the current value when the incoming one already has a value."""
        resolver = make_resolver(mock_conn)
        current_master = {"updated_at": datetime(2026, 7, 1)}  # naive
        raw_profile = {"created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}  # aware
        rule = IdentityRule(
            "full_name",
            "exact",
            consolidation_rule="most_recent",
            consolidation_config={"timestamp_field": "updated_at"},
        )

        result = resolver._resolve_scalar_consolidation(
            "full_name",
            "Old Name",
            "New Name",
            rule,
            raw_profile,
            current_master,
        )

        assert result == "Old Name"

    def test_verified_first_guards_against_recursive_fallback_mode(self, mock_conn):
        """A misconfigured fallback_mode pointing back at verified_first must
        not cause infinite recursion -- it should be treated as non_null."""
        resolver = make_resolver(mock_conn)
        current_master = {"kyc_status": "pending", "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc)}
        raw_profile = {"kyc_status": "pending", "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc)}
        rule = IdentityRule(
            "email",
            "exact",
            consolidation_rule="verified_first",
            consolidation_config={
                "verified_field": "kyc_status",
                "verified_values": ["verified"],
                "fallback_mode": "verified_first",
            },
        )

        result = resolver._resolve_scalar_consolidation(
            "email",
            "old@example.com",
            "new@example.com",
            rule,
            raw_profile,
            current_master,
        )

        # non_null: existing value present -> kept.
        assert result == "old@example.com"

    def test_unknown_consolidation_rule_falls_back_to_non_null(self, mock_conn):
        resolver = make_resolver(mock_conn)
        rule = IdentityRule("full_name", "exact", consolidation_rule="not_a_real_strategy")

        result = resolver._resolve_scalar_consolidation(
            "full_name",
            "current name",
            "incoming name",
            rule,
            {},
            {},
        )

        assert result == "current name"


class TestConsolidationInLinkUpdate:
    def test_link_and_update_uses_consolidation_metadata(self, mock_cursor, mock_conn):
        resolver = make_resolver(mock_conn)
        raw_profile = {
            "raw_profile_id": "r1",
            "tenant_id": "t1",
            "domain": "banking",
            "source_system": "moengage",
            "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "full_name": "New Name",
            "email": "new@example.com",
            "phone_number": "new-phone",
            "national_id": "new-id",
            "device_id": None,
            "advertising_id": None,
            "cookie_id": None,
            "external_customer_id": None,
            "push_token": None,
        }
        rules = [
            IdentityRule(
                "full_name",
                "exact",
                consolidation_rule="most_recent",
                consolidation_config={"timestamp_field": "updated_at"},
            ),
            IdentityRule(
                "email",
                "exact",
                consolidation_rule="verified_first",
                consolidation_config={
                    "verified_field": "kyc_status",
                    "verified_values": ["verified"],
                    "fallback_mode": "most_recent",
                    "timestamp_field": "updated_at",
                },
            ),
            IdentityRule(
                "phone_number",
                "exact",
                consolidation_rule="source_priority",
                consolidation_config={"source_priority": ["core_banking", "moengage"]},
            ),
            IdentityRule("national_id", "exact", consolidation_rule="overwrite"),
        ]
        mock_cursor.fetchone.return_value = {
            "full_name": "Old Name",
            "email": "old@example.com",
            "phone_number": "old-phone",
            "national_id": "old-id",
            "kyc_status": "verified",
            "source_systems": ["core_banking"],
            "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        }

        resolver._link_and_update(mock_cursor, raw_profile, "master-1", rules=rules)

        assert mock_cursor.execute.call_count == 5
        _, update_params = mock_cursor.execute.call_args_list[3][0]
        assert update_params[:3] == ("New Name", "old@example.com", "old-phone")
        _, upsert_params = mock_cursor.execute.call_args_list[4][0]
        assert upsert_params[0] == "t1"
        assert upsert_params[1] == "master-1"
        assert upsert_params[2] == "banking"
        assert upsert_params[3].adapted == {"national_id": "new-id"}

    def test_link_and_update_joins_append_distinct_list_into_string_for_scalar_field(
        self, mock_cursor, mock_conn
    ):
        """append_distinct on a SCALAR_MERGE_FIELDS (TEXT column) must never
        pass a Python list as the UPDATE parameter -- it must be flattened to
        a plain string first."""
        resolver = make_resolver(mock_conn)
        raw_profile = {
            "raw_profile_id": "r1",
            "tenant_id": "t1",
            "domain": "retail",
            "source_system": "WebTracking",
            "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "full_name": "Second Name",
            "email": None,
            "phone_number": None,
            "national_id": None,
            "device_id": None,
            "advertising_id": None,
            "cookie_id": None,
            "external_customer_id": None,
            "push_token": None,
        }
        rules = [IdentityRule("full_name", "exact", consolidation_rule="append_distinct")]
        mock_cursor.fetchone.return_value = {
            "full_name": "First Name",
            "email": None,
            "phone_number": None,
            "national_id": None,
            "kyc_status": None,
            "source_systems": [],
            "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        }

        resolver._link_and_update(mock_cursor, raw_profile, "master-1", rules=rules)

        _, update_params = mock_cursor.execute.call_args_list[3][0]
        assert update_params[0] == "First Name, Second Name"

    def test_link_and_update_fetches_custom_verified_field_column(self, mock_cursor, mock_conn):
        """A custom verified_field/timestamp_field named in consolidation_config
        must actually be SELECTed from cdp_master_profiles, not silently
        dropped because it isn't one of the hardcoded base columns."""
        resolver = make_resolver(mock_conn)
        raw_profile = {
            "raw_profile_id": "r1",
            "tenant_id": "t1",
            "domain": "banking",
            "source_system": "CoreBanking",
            "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "full_name": None,
            "email": "new@example.com",
            "phone_number": None,
            "national_id": None,
            "device_id": None,
            "advertising_id": None,
            "cookie_id": None,
            "external_customer_id": None,
            "push_token": None,
        }
        rules = [
            IdentityRule(
                "email",
                "exact",
                consolidation_rule="verified_first",
                consolidation_config={"verified_field": "risk_review_status", "verified_values": ["passed"]},
            )
        ]
        mock_cursor.fetchone.return_value = {
            "full_name": None,
            "email": "old@example.com",
            "phone_number": None,
            "national_id": None,
            "kyc_status": None,
            "source_systems": [],
            "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
            "risk_review_status": "passed",
        }

        resolver._link_and_update(mock_cursor, raw_profile, "master-1", rules=rules)

        select_query = mock_cursor.execute.call_args_list[1][0][0]
        assert "risk_review_status" in select_query

    def test_link_and_update_ignores_unsafe_column_name_in_config(self, mock_cursor, mock_conn):
        """A malformed/malicious column name in consolidation_config must
        never be interpolated into the dynamic SELECT."""
        resolver = make_resolver(mock_conn)
        raw_profile = {
            "raw_profile_id": "r1",
            "tenant_id": "t1",
            "domain": "banking",
            "source_system": "CoreBanking",
            "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "full_name": None,
            "email": "new@example.com",
            "phone_number": None,
            "national_id": None,
            "device_id": None,
            "advertising_id": None,
            "cookie_id": None,
            "external_customer_id": None,
            "push_token": None,
        }
        rules = [
            IdentityRule(
                "email",
                "exact",
                consolidation_rule="verified_first",
                consolidation_config={"verified_field": "status; DROP TABLE cdp_master_profiles;--"},
            )
        ]
        mock_cursor.fetchone.return_value = {
            "full_name": None,
            "email": "old@example.com",
            "phone_number": None,
            "national_id": None,
            "kyc_status": None,
            "source_systems": [],
            "updated_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        }

        resolver._link_and_update(mock_cursor, raw_profile, "master-1", rules=rules)

        select_query = mock_cursor.execute.call_args_list[1][0][0]
        assert "DROP TABLE" not in select_query
