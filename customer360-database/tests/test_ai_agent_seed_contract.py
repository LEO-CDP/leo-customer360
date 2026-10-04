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

    def test_model_identifiers_are_real_and_templates_are_inactive(self):
        seed_body = self.seed_sql.split("WITH seed_agents", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_agents", 1
        )[0]
        identifiers = re.findall(
            r"^\s*'(classification|regression|clustering|ranking_recommendation|"
            r"forecasting|anomaly_detection|uplift_modeling|semantic_embedding|"
            r"graph_ml|optimization|rules_engine|generative_llm)',\s*"
            r"('([^']+)'|NULL),\s*'([^']+)'",
            seed_body,
            re.MULTILINE,
        )
        actual = {kind: model or None for kind, _, model, _ in identifiers}
        self.assertEqual(actual, {
            "classification": "xgboost.XGBClassifier",
            "regression": "lightgbm.LGBMRegressor",
            "clustering": "sklearn.cluster.MiniBatchKMeans",
            "ranking_recommendation": "lightgbm.LGBMRanker",
            "forecasting": "lightgbm.LGBMRegressor",
            "anomaly_detection": "sklearn.ensemble.IsolationForest",
            "uplift_modeling": "econml.metalearners.XLearner",
            "semantic_embedding": "text-embedding-3-small",
            "graph_ml": "torch_geometric.nn.models.GraphSAGE",
            "optimization": None,
            "rules_engine": None,
            "generative_llm": "openai/gpt-4.1-mini-2025-04-14",
        })
        self.assertTrue(all(status == "INACTIVE" for *_, status in identifiers))
        self.assertIn("'embedding_dimension', 1536", seed_body)
        self.assertIn("'response_format', jsonb_build_object('type', 'json_object')", seed_body)

    def test_bootstrap_preserves_existing_configuration_and_prompt_history(self):
        self.assertNotIn("DO UPDATE SET", self.seed_sql)
        self.assertRegex(self.seed_sql, r"(?m)^BEGIN;")
        self.assertRegex(self.seed_sql, r"(?m)^COMMIT;")

    def test_core_attribute_agent_ownership_references_are_preserved(self):
        core_sql = (
            self.repository_root / "customer360-database" / "init-core-database.sql"
        ).read_text(encoding="utf-8")
        references = set(re.findall(r"TRUE, '([a-z_]+)', 'v\d+'", core_sql))
        self.assertGreater(len(references), 5)
        for agent_code in references:
            self.assertIn(f"'{agent_code}'", self.seed_sql)

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

        self.assertEqual(len(arrays), 24)
        self.assertTrue(declared_input_features <= catalog_keys)
        self.assertIn("sql_expression", self.seed_sql)
        self.assertIn("pandas_expression", self.seed_sql)

    def test_seed_contains_all_twelve_agent_feature_arrays(self):
        seed_body = self.seed_sql.split("WITH seed_agents", 1)[1].split(
            "INSERT INTO customer360.cdp_ai_agents", 1
        )[0]
        arrays = re.findall(r"ARRAY\[(.*?)\]::text\[\]", seed_body, re.DOTALL)

        self.assertEqual(len(arrays), 24)
        self.assertNotRegex(
            "\n".join(arrays),
            r"'(?:customer_profile|recent_events|purchase_history|"
            r"historical_orders|behavior_history|customer_nodes)'",
        )


if __name__ == "__main__":
    unittest.main()
