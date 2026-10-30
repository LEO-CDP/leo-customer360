"""Regression checks for AI-agent bootstrap and feature derivation contracts."""

import ast
import re
import unittest
from pathlib import Path


class AiAgentSeedContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repository_root = Path(__file__).resolve().parents[2]
        cls.seed_sql = (
            repository_root / "customer360-database" / "init-cdp-ai-agents.sql"
        ).read_text(encoding="utf-8")
        cls.repository_root = repository_root

    def historical_step(self, filename):
        upgrade = (
            self.repository_root / "customer360-database" / "legacy-upgrade.sql"
        ).read_text(encoding="utf-8")
        return upgrade.split(f"-- Historical step: {filename}", 1)[1].split(
            "-- Historical step: ", 1
        )[0]

    def test_pandas_guidance_is_valid_python(self):
        expressions = re.findall(
            r"\$pandas\$(.*?)\$pandas\$", self.seed_sql, re.DOTALL
        )
        self.assertGreater(len(expressions), 50)
        for expression in expressions:
            with self.subTest(expression=expression):
                ast.parse(expression, mode="eval")

    def test_feature_aggregation_includes_tenant_scope(self):
        expressions = re.findall(r"\$pandas\$(.*?)\$pandas\$", self.seed_sql)
        for expression in expressions:
            if ".groupby(" in expression:
                with self.subTest(expression=expression):
                    self.assertRegex(expression, r'\.groupby\(\["tenant_id",')

    def test_time_windows_exclude_future_observations(self):
        for expression in re.findall(r"\$sql\$(.*?)\$sql\$", self.seed_sql):
            for alias, column in (
                ("events", "event_time"),
                ("tx", "transaction_time"),
                ("contacts", "contact_date"),
            ):
                if f"{alias}.{column} >= :as_of" in expression:
                    self.assertIn(f"{alias}.{column} <= :as_of", expression)
        for expression in re.findall(r"\$pandas\$(.*?)\$pandas\$", self.seed_sql):
            if "pd.Timedelta(days=" in expression:
                self.assertIn("<= as_of", expression)

    def test_model_identifiers_and_active_handlers_are_real(self):
        seed_body = self.seed_sql.split("WITH seed_agents", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_agents", 1
        )[0]
        identifiers = re.findall(
            r"^\s*'(classification|regression|clustering|ranking_recommendation|"
            r"forecasting|anomaly_detection|uplift_modeling|"
            r"optimization|rules_engine|generative_llm)',\s*"
            r"('([^']+)'|NULL),\s*'([^']+)'",
            seed_body,
            re.MULTILINE,
        )
        actual = {kind: model or None for kind, _, model, _ in identifiers}
        actual_status = {kind: status for kind, _, _, status in identifiers}
        self.assertEqual(actual, {
            "classification": "xgboost.XGBClassifier",
            "regression": "lightgbm.LGBMRegressor",
            "clustering": "sklearn.cluster.MiniBatchKMeans",
            "ranking_recommendation": "lightgbm.LGBMRanker",
            "forecasting": "lightgbm.LGBMRegressor",
            "anomaly_detection": "sklearn.ensemble.IsolationForest",
            "uplift_modeling": "econml.metalearners.XLearner",
            "optimization": None,
            "rules_engine": None,
            "generative_llm": "gpt-6-luna",
        })
        self.assertEqual(
            actual_status,
            {
                kind: (
                    "ACTIVE"
                    if kind in {"ranking_recommendation", "rules_engine"}
                    else "INACTIVE"
                )
                for kind in actual
            },
        )
        self.assertIn("'strategy', 'tags'", seed_body)
        self.assertIn("'top_k', 8", seed_body)
        self.assertIn("'semantic_weight', 0.7", seed_body)
        self.assertIn("'tag_weight', 0.3", seed_body)
        self.assertIn("'minimum_score', 0.0", seed_body)
        self.assertIn("'consent_requires_literal_true', true", seed_body)
        self.assertIn("'advertising_consent_is_provider_specific', true", seed_body)
        self.assertIn("'response_format', jsonb_build_object('type', 'json_object')", seed_body)

    def test_runner_requirements_include_sql_referenced_ml_libraries(self):
        requirements_text = (
            self.repository_root
            / "customer360-backend"
            / "ai_agents_runners"
            / "requirements.txt"
        ).read_text(encoding="utf-8")
        for package in (
            "numpy",
            "pandas",
            "scikit-learn",
            "lightgbm",
            "xgboost",
            "econml",
        ):
            with self.subTest(package=package):
                self.assertRegex(
                    requirements_text,
                    rf"(?m)^{re.escape(package)}(?:[><=~!]|$)",
                )
        self.assertNotRegex(requirements_text, r"(?mi)^torch(?:-geometric)?(?:[><=~!]|$)")

    def test_bootstrap_preserves_existing_configuration_and_prompt_history(self):
        self.assertNotIn("DO UPDATE SET", self.seed_sql)
        self.assertRegex(self.seed_sql, r"(?m)^BEGIN;")
        self.assertRegex(self.seed_sql, r"(?m)^COMMIT;")

    def test_unassigned_attribute_ownership_does_not_require_placeholder_agents(self):
        core_sql = (
            self.repository_root / "customer360-database" / "init-core-database.sql"
        ).read_text(encoding="utf-8")
        self.assertNotIn("compatibility_agents", self.seed_sql)
        for agent_code in (
            "identity_resolution",
            "data_quality",
            "persona_intelligence",
            "lifecycle_intelligence",
            "churn_scoring",
            "clv_scoring",
            "cx_intelligence",
        ):
            self.assertNotRegex(core_sql, rf"TRUE,\s*'{agent_code}',\s*'v\d+'")

    def test_generative_seeds_use_gpt_6_luna(self):
        self.assertEqual(self.seed_sql.count("'gpt-6-luna'"), 2)

    def test_profile_recommendations_are_tenant_scoped_and_segment_aware(self):
        schema = (
            self.repository_root / "customer360-database" / "database-schema.sql"
        ).read_text(encoding="utf-8")
        migration = self.historical_step("006_cdp_agent_workflow.sql")
        for sql in (schema, migration):
            self.assertIn(
                "CREATE TABLE IF NOT EXISTS customer360.cdp_profile_recommendation_runs",
                sql,
            )
            self.assertIn("CREATE TABLE IF NOT EXISTS customer360.cdp_profile_recommendations", sql)
            self.assertIn("PRIMARY KEY (", sql)
            self.assertIn("status IN ('RUNNING', 'SUCCEEDED', 'FAILED')", sql)
            self.assertIn("fk_cdp_profile_recommendations_tenant_run", sql)
            self.assertIn("fk_cdp_profile_recommendations_tenant_profile", sql)
            self.assertIn("fk_cdp_profile_recommendations_tenant_content", sql)
            self.assertIn("cdp_profile_recommendation_runs FORCE ROW LEVEL SECURITY", sql)
            self.assertIn("ALTER TABLE customer360.cdp_profile_recommendations FORCE ROW LEVEL SECURITY", sql)

    def test_content_items_store_provider_versioned_pgvector_embeddings(self):
        schema = (
            self.repository_root / "customer360-database" / "database-schema.sql"
        ).read_text(encoding="utf-8")
        workflow_migration = self.historical_step("006_cdp_agent_workflow.sql")
        self.assertIn("embedding VECTOR,", schema)
        self.assertIn("embedding_model TEXT", schema)
        self.assertIn("ADD COLUMN IF NOT EXISTS embedding VECTOR", workflow_migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS embedding_model TEXT", workflow_migration)
        for sql in (schema, workflow_migration):
            self.assertIn("invalidate_content_item_embedding()", sql)
            self.assertIn("trg_invalidate_content_item_embedding", sql)

    def test_content_item_embedding_metadata_is_added_before_schema_references(self):
        schema = (
            self.repository_root / "customer360-database" / "database-schema.sql"
        ).read_text(encoding="utf-8")
        upgrade = schema.index("ALTER TABLE customer360.cdp_content_items\n")
        references = schema.index(
            "COMMENT ON COLUMN customer360.cdp_content_items.embedding_text"
        )

        self.assertLess(upgrade, references)
        for column, sql_type in (
            ("embedding_text", "TEXT"),
            ("embedding_version", "VARCHAR(50)"),
            ("embedding_updated_at", "TIMESTAMPTZ"),
        ):
            self.assertIn(
                f"ADD COLUMN IF NOT EXISTS {column} {sql_type}",
                schema[upgrade:references],
            )

    def test_existing_agent_endpoints_have_seeded_prompt_keys(self):
        for key in (
            "campaign.plan.instructions",
            "campaign.zns.instructions",
            "segment.nl_to_rules.instructions",
        ):
            self.assertIn(f"'{key}'", self.seed_sql)

    def test_feature_keys_are_unique_and_contract_arrays_match(self):
        catalog_body = self.seed_sql.split("WITH feature_definitions", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_feature_catalog", 1
        )[0]
        keys = re.findall(r"^\s*\('([a-z0-9_]+)',", catalog_body, re.MULTILINE)
        self.assertEqual(len(keys), len(set(keys)))
        seed_body = self.seed_sql.split("WITH seed_agents", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_agents", 1
        )[0]
        arrays = re.findall(r"ARRAY\[(.*?)\]::text\[\]", seed_body, re.DOTALL)
        for inputs, required in zip(arrays[::2], arrays[1::2]):
            self.assertEqual(inputs, required)
            features = re.findall(r"'([a-z0-9_]+)'", inputs)
            self.assertEqual(len(features), len(set(features)))

    def test_every_declared_input_feature_has_sql_and_pandas_definition(self):
        seed_body = self.seed_sql.split("WITH seed_agents", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_agents", 1
        )[0]
        arrays = re.findall(r"ARRAY\[(.*?)\]::text\[\]", seed_body, re.DOTALL)
        catalog_body = self.seed_sql.split("WITH feature_definitions", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_feature_catalog", 1
        )[0]
        catalog_keys = set(
            re.findall(
                r"^\s*\('([a-z0-9_]+)',\s*"
                r"'(?:profile|event_log|transaction|contact|graph|aggregate|candidate|runtime)'",
                catalog_body,
                re.MULTILINE,
            )
        )
        declared_input_features = set(
            re.findall(r"'([a-z0-9_]+)'", "\n".join(arrays[::2]))
        )

        self.assertEqual(len(arrays), 20)
        self.assertTrue(declared_input_features <= catalog_keys)
        self.assertIn("sql_expression", self.seed_sql)
        self.assertIn("pandas_expression", self.seed_sql)

    def test_seed_contains_all_ten_agent_feature_arrays(self):
        seed_body = self.seed_sql.split("WITH seed_agents", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_agents", 1
        )[0]
        arrays = re.findall(r"ARRAY\[(.*?)\]::text\[\]", seed_body, re.DOTALL)

        self.assertEqual(len(arrays), 20)
        self.assertNotRegex(
            "\n".join(arrays),
            r"'(?:customer_profile|recent_events|purchase_history|"
            r"historical_orders|behavior_history|customer_nodes)'",
        )

    def test_graph_agent_type_removal_is_guarded_for_existing_databases(self):
        migration = self.historical_step("013_remove_graph_ml_agent_type.sql")
        schema = (
            self.repository_root / "customer360-database" / "database-schema.sql"
        ).read_text(encoding="utf-8")

        self.assertNotIn("'graph_ml'", schema)
        self.assertNotIn("'identity_graph_intelligence'", self.seed_sql)
        self.assertIn("cdp_agent_workflow", migration)
        self.assertIn("cdp_profile_attributes", migration)
        self.assertIn("DELETE FROM customer360.cdp_ai_agents", migration)

    def test_semantic_embedding_agent_removal_is_guarded_for_existing_databases(self):
        migration = self.historical_step("015_remove_semantic_embedding_agent_type.sql")
        schema = (
            self.repository_root / "customer360-database" / "database-schema.sql"
        ).read_text(encoding="utf-8")

        self.assertNotIn("'semantic_embedding'", schema)
        self.assertNotIn("'semantic_embedding'", migration)
        self.assertNotIn("'customer_semantic_profile'", self.seed_sql)
        self.assertIn("cdp_agent_workflow", migration)
        self.assertIn("cdp_profile_attributes", migration)
        self.assertIn("DELETE FROM customer360.cdp_ai_agents", migration)

    def test_eligibility_activation_migration_preserves_operator_statuses(self):
        migration = self.historical_step("014_activate_eligibility_policy.sql")
        self.assertIn("agent_code = 'eligibility_policy'", migration)
        self.assertIn("status = 'INACTIVE'", migration)
        self.assertNotIn("status = 'FAILED'", migration)
        self.assertNotIn("status = 'DEPRECATED'", migration)


if __name__ == "__main__":
    unittest.main()
