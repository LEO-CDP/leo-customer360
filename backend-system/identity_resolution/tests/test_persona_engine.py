"""Unit tests for identity_resolution.persona_engine (the AI-native Customer
Persona Resolution Engine: identity *understanding* on top of identity
*matching*).

Pure scoring/computation functions are tested directly (no DB, no mocks).
PersonaResolutionEngine is tested the same way as
identity_resolution.resolver.CustomerIdentityResolver in test_resolver.py:
psycopg2 cursor/connection are mocked via the `mock_cursor`/`mock_conn`
fixtures in conftest.py.
"""

from datetime import date, datetime, timedelta

import pytest

from identity_resolution import persona
from identity_resolution.persona_engine import (
    PERSONA_CONFIG_DEFAULTS,
    PersonaResolutionEngine,
    apply_persona_config,
    compute_behavior_score,
    compute_customer_value_tier,
    compute_engagement_score,
    compute_financial_score,
    compute_loyalty_score,
    compute_next_best_action,
    compute_persona,
    compute_persona_category,
    compute_persona_code,
    compute_persona_score,
    compute_relationship_score,
    compute_risk_level,
    compute_risk_score,
    load_persona_config,
)


def _profile(**overrides):
    base = {
        "master_profile_id": "11111111-1111-1111-1111-111111111111",
        "domain": "retail",
        "lifecycle_stage": "customer",
        "membership_tier": "gold",
        "customer_since": date.today() - timedelta(days=365),
        "last_activity_at": datetime.now() - timedelta(days=1),
        "preferred_channel": "Mobile App",
        "source_systems": ["AppsFlyer", "WebTracking"],
        "secondary_emails": [],
        "secondary_phones": [],
        "historical_clv": 2000.0,
        "predictive_clv": 3000.0,
        "engagement_score": 60.0,
        "churn_probability": 0.2,
        "churn_risk_tier": "low",
        "risk_segment": "low",
        "kyc_status": "verified",
        "identity_confidence_score": 0.9,
        "profile_completeness_score": 80.0,
        "is_hashed": False,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_persona_engine_config():
    # Ensure test isolation since apply_persona_config mutates module-level constants.
    apply_persona_config(PERSONA_CONFIG_DEFAULTS)
    yield
    apply_persona_config(PERSONA_CONFIG_DEFAULTS)


class TestComponentScores:
    def test_behavior_score_uses_lifecycle_base(self):
        assert compute_behavior_score(_profile(lifecycle_stage="vip", engagement_score=None)) == 95.0
        assert compute_behavior_score(_profile(lifecycle_stage="prospect", engagement_score=None)) == 20.0

    def test_behavior_score_blends_with_existing_engagement_score(self):
        score = compute_behavior_score(_profile(lifecycle_stage="customer", engagement_score=100.0))
        assert score == (65.0 + 100.0) / 2.0

    def test_behavior_score_defaults_for_unknown_lifecycle(self):
        assert compute_behavior_score(_profile(lifecycle_stage=None, engagement_score=None)) == 30.0

    def test_engagement_score_high_for_recent_activity(self):
        recent = compute_engagement_score(_profile(last_activity_at=datetime.now(), source_systems=[]))
        stale = compute_engagement_score(
            _profile(last_activity_at=datetime.now() - timedelta(days=400), source_systems=[])
        )
        assert recent > stale

    def test_engagement_score_defaults_when_no_last_activity(self):
        score = compute_engagement_score(_profile(last_activity_at=None, source_systems=[]))
        assert score == 30.0 * 0.7

    def test_engagement_score_channel_bonus_capped(self):
        many_channels = compute_engagement_score(
            _profile(last_activity_at=None, source_systems=["A", "B", "C", "D", "E", "F"])
        )
        assert many_channels <= 100.0

    def test_financial_score_prefers_predictive_over_historical(self):
        score = compute_financial_score(_profile(predictive_clv=5000.0, historical_clv=1.0))
        assert score == 100.0

    def test_financial_score_falls_back_to_historical(self):
        score = compute_financial_score(_profile(predictive_clv=None, historical_clv=2500.0))
        assert score == 50.0

    def test_financial_score_clipped_to_100(self):
        score = compute_financial_score(_profile(predictive_clv=50000.0))
        assert score == 100.0

    def test_financial_score_zero_when_no_clv(self):
        score = compute_financial_score(_profile(predictive_clv=None, historical_clv=None))
        assert score == 0.0

    def test_loyalty_score_platinum_beats_bronze(self):
        platinum = compute_loyalty_score(_profile(membership_tier="platinum", customer_since=None))
        bronze = compute_loyalty_score(_profile(membership_tier="bronze", customer_since=None))
        assert platinum > bronze

    def test_loyalty_score_tenure_bonus(self):
        no_tenure = compute_loyalty_score(_profile(membership_tier="gold", customer_since=None))
        long_tenure = compute_loyalty_score(
            _profile(membership_tier="gold", customer_since=date.today() - timedelta(days=730))
        )
        assert long_tenure > no_tenure

    def test_relationship_score_more_channels_and_contacts_score_higher(self):
        low = compute_relationship_score(_profile(source_systems=[], secondary_emails=[], secondary_phones=[]))
        high = compute_relationship_score(
            _profile(source_systems=["A", "B", "C"], secondary_emails=[{"email": "x@y.com"}], secondary_phones=[])
        )
        assert high > low

    def test_risk_score_uses_churn_probability(self):
        low_risk = compute_risk_score(_profile(churn_probability=0.1, risk_segment="low", kyc_status="verified"))
        high_risk = compute_risk_score(_profile(churn_probability=0.9, risk_segment="high", kyc_status="rejected"))
        assert high_risk > low_risk
        assert high_risk <= 100.0

    def test_risk_score_defaults_when_no_churn_probability(self):
        score = compute_risk_score(_profile(churn_probability=None, risk_segment=None, kyc_status=None))
        assert score == 20.0


class TestPersonaConfigLoader:
    def test_load_persona_config_falls_back_to_defaults_when_query_fails(self, mock_cursor):
        mock_cursor.execute.side_effect = RuntimeError("db unavailable")

        config = load_persona_config(mock_cursor, schema="customer360")

        assert config["RISK_LEVEL_HIGH_THRESHOLD"] == 60.0
        assert config["PERSONA_HISTORY_SCORE_DELTA_THRESHOLD"] == 5.0

    def test_load_persona_config_overrides_defaults_from_db_rows(self, mock_cursor):
        mock_cursor.fetchall.return_value = [
            {
                "config_key": "RISK_LEVEL_HIGH_THRESHOLD",
                "config_value": "55.5",
                "data_type": "NUMERIC",
            },
            {
                "config_key": "ENGAGEMENT_RECENCY_THRESHOLD_30D",
                "config_value": "45",
                "data_type": "INTEGER",
            },
            {
                "config_key": "UNKNOWN_KEY_SHOULD_BE_IGNORED",
                "config_value": "999",
                "data_type": "NUMERIC",
            },
        ]

        config = load_persona_config(mock_cursor, schema="customer360")

        assert config["RISK_LEVEL_HIGH_THRESHOLD"] == 55.5
        assert config["ENGAGEMENT_RECENCY_THRESHOLD_30D"] == 45
        assert "UNKNOWN_KEY_SHOULD_BE_IGNORED" not in config

    def test_apply_persona_config_updates_runtime_thresholds(self):
        config = dict(PERSONA_CONFIG_DEFAULTS)
        config["RISK_LEVEL_HIGH_THRESHOLD"] = 55.0

        apply_persona_config(config)

        assert compute_risk_level(59.0) == "high"
        assert compute_risk_level(54.0) == "medium"


class TestPersonaScoreAggregation:
    def test_persona_score_is_100_when_all_positive_and_no_risk(self):
        scores = {"behavior": 100, "engagement": 100, "financial": 100, "loyalty": 100, "relationship": 100, "risk": 0}
        assert compute_persona_score(scores) == 100.0

    def test_persona_score_is_0_when_all_zero_and_max_risk(self):
        scores = {"behavior": 0, "engagement": 0, "financial": 0, "loyalty": 0, "relationship": 0, "risk": 100}
        assert compute_persona_score(scores) == 0.0

    def test_customer_value_tier_thresholds(self):
        assert compute_customer_value_tier({"financial": 90, "loyalty": 90}) == "champion"
        assert compute_customer_value_tier({"financial": 70, "loyalty": 60}) == "high_value"
        assert compute_customer_value_tier({"financial": 40, "loyalty": 30}) == "growth_potential"
        assert compute_customer_value_tier({"financial": 10, "loyalty": 10}) == "standard"

    def test_risk_level_thresholds(self):
        assert compute_risk_level(80) == "critical"
        assert compute_risk_level(60) == "high"
        assert compute_risk_level(40) == "medium"
        assert compute_risk_level(30) == "low"

    def test_next_best_action_prioritizes_churn_retention(self):
        action = compute_next_best_action(lifecycle_stage="customer", value_tier="high_value", risk_level="high")
        assert "retention" in action.lower() or "win-back" in action.lower()

    def test_next_best_action_dormant(self):
        action = compute_next_best_action(lifecycle_stage="dormant", value_tier="standard", risk_level="low")
        assert "re-engagement" in action.lower() or "win-back" in action.lower()

    def test_next_best_action_default_cadence(self):
        action = compute_next_best_action(lifecycle_stage="customer", value_tier="standard", risk_level="low")
        assert action == "Continue standard engagement cadence."

    def test_persona_category_combines_tier_and_domain_role(self):
        category = compute_persona_category("banking", "champion")
        assert "Banking" in category or "Client" in category

    def test_persona_code_is_stable_slug(self):
        code = compute_persona_code("retail", "high_value", "customer")
        assert code == "retail_high_value_customer"

    def test_persona_code_handles_missing_lifecycle(self):
        code = compute_persona_code("retail", "standard", None)
        assert code == "retail_standard_unscored"


class TestComputePersona:
    def test_returns_populated_computation(self, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        computation = compute_persona(_profile())

        assert computation.persona_name
        assert computation.persona_summary
        assert computation.llm_provider == "offline-heuristic"
        assert computation.llm_model == "persona-engine-rule-based-v1"
        assert 0.0 <= computation.persona_score <= 100.0
        assert computation.customer_value_tier in ("champion", "high_value", "growth_potential", "standard")
        assert computation.risk_level in ("low", "medium", "high", "critical")
        assert computation.features  # non-empty

    def test_persona_name_is_deterministic_per_master_profile_id(self, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        first = compute_persona(_profile())
        second = compute_persona(_profile())
        assert first.persona_name == second.persona_name

    def test_persona_name_differs_across_master_profiles(self, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        a = compute_persona(_profile(master_profile_id="11111111-1111-1111-1111-111111111111"))
        b = compute_persona(_profile(master_profile_id="22222222-2222-2222-2222-222222222222"))
        assert a.persona_name != b.persona_name

    def test_persona_summary_and_name_never_contain_raw_pii_fields(self, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        profile = _profile()
        computation = compute_persona(profile)
        # The master_profile dict passed in never carries full_name/email/
        # phone_number/national_id at all (see MASTER_PROFILE_SELECT_COLUMNS),
        # so this is really asserting compute_persona doesn't invent/leak any
        # such value into its outputs.
        for forbidden in ("full_name", "email", "phone_number", "national_id"):
            assert forbidden not in computation.persona_name
            assert forbidden not in computation.persona_summary

    def test_uses_genai_when_configured(self, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", "a-real-key")

        class _FakeClient:
            def __init__(self):
                self.models = self

            def generate_content(self, model, contents, config):
                schema = config.response_schema
                if schema.__name__ == "_PersonaLabel":
                    parsed = schema(persona_name="Digital-First Banking Client")
                else:
                    parsed = schema(persona_summary="A digital-first, low-risk banking client.")
                return type("R", (), {"parsed": parsed})()

        monkeypatch.setattr(persona, "_get_genai_client", lambda: _FakeClient())

        computation = compute_persona(_profile(domain="banking"))

        assert computation.llm_provider == "google-genai"
        assert "Digital-First Banking Client" in computation.persona_name
        assert computation.persona_summary == "A digital-first, low-risk banking client."


class TestPersonaResolutionEngine:
    def make_engine(self):
        return PersonaResolutionEngine(schema="customer360")

    def test_resolve_persona_returns_none_when_profile_missing(self, mock_cursor, mock_conn):
        mock_cursor.fetchone.return_value = None
        engine = self.make_engine()

        result = engine.resolve_persona(mock_cursor, "t1", "missing-master")

        assert result is None

    def test_resolve_persona_inserts_persona_and_updates_master(self, mock_cursor, mock_conn, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        master_row = _profile()
        mock_cursor.fetchall.return_value = []
        # fetchone is called in order: _fetch_master_profile, _fetch_current_persona,
        # _upsert_archetype (RETURNING persona_archetype_id), _next_computed_version,
        # _insert_persona (RETURNING persona_id)
        mock_cursor.fetchone.side_effect = [
            master_row,
            None,  # no existing/current persona
            {"persona_archetype_id": "archetype-1"},
            {"next_version": 1},
            {"persona_id": "persona-1"},
        ]
        engine = self.make_engine()

        result = engine.resolve_persona(mock_cursor, "t1", master_row["master_profile_id"])

        assert result is not None
        assert result["persona_id"] == "persona-1"
        assert result["computed_version"] == 1

        executed_queries = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO customer360.cdp_persona_archetypes" in q for q in executed_queries)
        assert any("INSERT INTO customer360.cdp_customer_personas" in q for q in executed_queries)
        assert any("INSERT INTO customer360.cdp_persona_features" in q for q in executed_queries)
        assert any("INSERT INTO customer360.cdp_persona_score_details" in q for q in executed_queries)
        assert any("INSERT INTO customer360.cdp_persona_history" in q for q in executed_queries)
        assert any("UPDATE customer360.cdp_master_profiles" in q for q in executed_queries)
        assert any("UPDATE customer360.cdp_customer_personas" in q and "is_active = FALSE" in q for q in executed_queries)

    def test_resolve_persona_skips_history_when_change_is_not_material(self, mock_cursor, mock_conn, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        master_row = _profile()
        computation = compute_persona(master_row)
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.side_effect = [
            master_row,
            {"persona_id": "old-persona", "persona_name": computation.persona_name, "persona_score": computation.persona_score},
            {"persona_archetype_id": "archetype-2"},
            {"next_version": 2},
            {"persona_id": "persona-2"},
        ]
        engine = self.make_engine()

        engine.resolve_persona(mock_cursor, "t1", master_row["master_profile_id"])

        executed_queries = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert not any("INSERT INTO customer360.cdp_persona_history" in q for q in executed_queries)

    def test_resolve_persona_records_history_when_score_changes_materially(self, mock_cursor, mock_conn, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        master_row = _profile()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.side_effect = [
            master_row,
            {"persona_id": "old-persona", "persona_name": "Some Old Persona #abc123", "persona_score": 1.0},
            {"persona_archetype_id": "archetype-3"},
            {"next_version": 2},
            {"persona_id": "persona-3"},
        ]
        engine = self.make_engine()

        engine.resolve_persona(mock_cursor, "t1", master_row["master_profile_id"])

        executed_queries = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO customer360.cdp_persona_history" in q for q in executed_queries)

    def test_resolve_persona_never_raises_on_unexpected_failure(self, mock_cursor, mock_conn):
        mock_cursor.fetchone.side_effect = RuntimeError("db exploded")
        engine = self.make_engine()

        result = engine.resolve_persona(mock_cursor, "t1", "some-master")

        assert result is None

    def test_resolve_persona_applies_db_config_override(self, mock_cursor, mock_conn, monkeypatch):
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        master_row = _profile()
        mock_cursor.fetchall.return_value = [
            {
                "config_key": "RISK_LEVEL_HIGH_THRESHOLD",
                "config_value": "55",
                "data_type": "INTEGER",
            }
        ]
        mock_cursor.fetchone.side_effect = [
            master_row,
            None,
            {"persona_archetype_id": "archetype-9"},
            {"next_version": 1},
            {"persona_id": "persona-9"},
        ]
        engine = self.make_engine()

        result = engine.resolve_persona(mock_cursor, "t1", master_row["master_profile_id"])

        assert result is not None
        assert compute_risk_level(56.0) == "high"


class TestPersonaArchetypeMatching:
    """Covers the many-to-many persona-archetype plumbing added on top of
    PersonaResolutionEngine: _upsert_archetype (shared archetype upsert),
    _fetch_current_persona (joined through the archetype for its name), and
    _next_computed_version (versioned per master_profile_id +
    persona_archetype_id, not the old per-persona_code scheme)."""

    def make_engine(self):
        return PersonaResolutionEngine(schema="customer360")

    def test_upsert_archetype_inserts_with_conflict_upsert_on_tenant_domain_code(self, mock_cursor):
        mock_cursor.fetchone.return_value = {"persona_archetype_id": "archetype-1"}
        engine = self.make_engine()
        computation = compute_persona(_profile())

        result = engine._upsert_archetype(mock_cursor, "t1", "retail", computation)

        assert result == "archetype-1"
        query, params = mock_cursor.execute.call_args.args
        assert "INSERT INTO customer360.cdp_persona_archetypes" in query
        assert "ON CONFLICT (tenant_id, domain, persona_code) DO UPDATE" in query
        assert "RETURNING persona_archetype_id" in query
        assert params[:3] == ("t1", "retail", computation.persona_code)

    def test_upsert_archetype_shares_the_same_row_across_master_profiles(self, mock_cursor):
        """Two different master profiles resolving to the same persona_code
        must upsert into (and get back the id of) the SAME archetype row --
        this is what makes the relationship many-to-many rather than one
        archetype row per profile."""
        mock_cursor.fetchone.return_value = {"persona_archetype_id": "shared-archetype-1"}
        engine = self.make_engine()
        computation_a = compute_persona(_profile(master_profile_id="profile-a"))
        computation_b = compute_persona(_profile(master_profile_id="profile-b"))

        id_a = engine._upsert_archetype(mock_cursor, "t1", "retail", computation_a)
        id_b = engine._upsert_archetype(mock_cursor, "t1", "retail", computation_b)

        assert id_a == id_b == "shared-archetype-1"
        assert mock_cursor.execute.call_count == 2

    def test_fetch_current_persona_joins_archetype_for_persona_name(self, mock_cursor):
        mock_cursor.fetchone.return_value = {
            "persona_id": "persona-1", "persona_name": "Gen Z Sneaker Collector", "persona_score": 72.5,
        }
        engine = self.make_engine()

        result = engine._fetch_current_persona(mock_cursor, "t1", "master-1")

        assert result["persona_name"] == "Gen Z Sneaker Collector"
        query = mock_cursor.execute.call_args.args[0]
        assert "FROM customer360.cdp_customer_personas cp" in query
        assert "JOIN customer360.cdp_persona_archetypes pa" in query
        assert "pa.persona_archetype_id = cp.persona_archetype_id" in query

    def test_next_computed_version_scopes_by_master_profile_and_archetype(self, mock_cursor):
        mock_cursor.fetchone.return_value = {"next_version": 3}
        engine = self.make_engine()

        version = engine._next_computed_version(mock_cursor, "t1", "master-1", "archetype-1")

        assert version == 3
        query, params = mock_cursor.execute.call_args.args
        assert "master_profile_id = %s AND persona_archetype_id = %s" in query
        assert params == ("t1", "master-1", "archetype-1")

    def test_next_computed_version_defaults_to_one_when_no_prior_row(self, mock_cursor):
        mock_cursor.fetchone.return_value = None
        engine = self.make_engine()

        version = engine._next_computed_version(mock_cursor, "t1", "master-1", "archetype-1")

        assert version == 1

    def test_resolve_persona_inserts_match_row_referencing_upserted_archetype_id(self, mock_cursor, mock_conn, monkeypatch):
        """End-to-end: the persona_archetype_id returned by the archetype
        upsert must be the one written onto the cdp_customer_personas match
        row (not, say, a freshly generated id)."""
        monkeypatch.setattr(persona, "GOOGLE_GENAI_API_KEY", None)
        master_row = _profile()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.side_effect = [
            master_row,
            None,
            {"persona_archetype_id": "archetype-42"},
            {"next_version": 1},
            {"persona_id": "persona-1"},
        ]
        engine = self.make_engine()

        engine.resolve_persona(mock_cursor, "t1", master_row["master_profile_id"])

        insert_persona_call = next(
            call for call in mock_cursor.execute.call_args_list
            if "INSERT INTO customer360.cdp_customer_personas" in call.args[0]
        )
        assert "archetype-42" in insert_persona_call.args[1]

    def test_resolve_persona_never_raises_when_archetype_upsert_fails(self, mock_cursor):
        """A malformed archetype-upsert response (e.g. RETURNING clause
        missing the key) must be swallowed by resolve_persona's blanket
        except, same as any other unexpected persistence failure."""
        master_row = _profile()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.side_effect = [master_row, None, {}]  # {} has no "persona_archetype_id" key
        engine = PersonaResolutionEngine(schema="customer360")

        result = engine.resolve_persona(mock_cursor, "t1", master_row["master_profile_id"])

        assert result is None
