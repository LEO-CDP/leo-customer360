"""Prompt-store tests: template rendering, the validate rail, and the Pg store's
snapshot read path (bodies live in the DB, seeded by customer360-database; no in-code
fallback -- so these exercise logic without a live DB)."""

import unittest

from prompts.stores import _history, _validate_prompt_state
from prompts import (
    PgPromptStore,
    PromptNotFound,
    PromptTemplate,
    get_store,
    reset_store_cache,
    validate,
)


class RenderTests(unittest.TestCase):
    def test_substitutes_name_and_preserves_literal_braces(self):
        tpl = PromptTemplate(key="x", body='Hello $who, return {"ok": true}', required_vars=("who",))
        self.assertEqual(tpl.render({"who": "An"}), 'Hello An, return {"ok": true}')

    def test_missing_required_param_raises(self):
        with self.assertRaises(ValueError):
            PromptTemplate(key="x", body="Hi $who", required_vars=("who",)).render({})

    def test_runtime_context_metadata_does_not_require_render_params(self):
        template = PromptTemplate(
            key="campaign",
            body="Return a JSON campaign plan.",
            required_vars=("target_segment", "candidate_content_items"),
        )
        self.assertEqual(template.render(), "Return a JSON campaign plan.")


class ValidateTests(unittest.TestCase):
    def test_ok(self):
        validate("x", "Return a JSON object", "none", ())

    def test_rejects_unknown_engine(self):
        with self.assertRaises(ValueError):
            validate("x", "body", "jinja", ())

    def test_rejects_empty_body(self):
        with self.assertRaises(ValueError):
            validate("x", "   ", "none", ())

    def test_rejects_undeclared_placeholder(self):
        with self.assertRaises(ValueError):
            validate("x", "uses $foo", "none", ())

    def test_history_requires_increasing_versions_and_nonempty_bodies(self):
        with self.assertRaises(ValueError):
            _history([
                {"version": 2, "body": "new"},
                {"version": 1, "body": "old"},
            ])
        with self.assertRaises(ValueError):
            _history([{"version": 1, "body": "   "}])

    def test_prompt_state_requires_current_revision_match(self):
        history = [{"version": 1, "body": "original", "required_vars": []}]
        with self.assertRaises(ValueError):
            _validate_prompt_state("x", 1, "changed", (), history)

    def test_seed_prompt_catalog_keys_are_valid(self):
        prompt_keys = [
            "campaign.plan.instructions",
            "campaign.zns.instructions",
            "persona.summary.instructions",
            "segment.nl_to_rules.instructions",
            "recommendation.nba.instructions",
            "retention.churn_intervention.instructions",
            "email.content.personalization.instructions",
            "marketing.compliance.review.instructions",
            "cir.identity_adjudication.instructions",
            "analytics.event_taxonomy.instructions",
        ]
        for key in prompt_keys:
            validate(key, f"Valid instructions for {key}", "none", ())

    def test_render_preserves_nested_json_in_prompt_body(self):
        body = 'Respond with JSON: {"decision": "APPROVED", "pii_risk": "NONE"}'
        tpl = PromptTemplate(key="test", body=body)
        self.assertEqual(tpl.render(), body)


class PgStoreReadTests(unittest.TestCase):
    def test_get_raises_when_unpublished(self):
        self.assertRaises(PromptNotFound, PgPromptStore().get, "nope")

    def test_get_returns_snapshot_row(self):
        store = PgPromptStore()
        store._snapshot = {"k": PromptTemplate(key="k", body="hi", version=3)}
        self.assertEqual(store.get("k").version, 3)

    def test_pinned_reports_snapshot_versions(self):
        store = PgPromptStore()
        store._snapshot = {"k": PromptTemplate(key="k", body="hi", version=2)}
        self.assertEqual(store.pinned(), {"k": 2})

    def test_refresh_populates_snapshot_from_db_rows(self):
        store = PgPromptStore()
        mock_conn = unittest.mock.MagicMock()
        mock_conn.execute.return_value.all.return_value = [
            (
                "campaign.plan.instructions",
                "none",
                1,
                "Plan campaign body",
                ["target_segment"],
                [{"version": 1, "body": "Plan campaign body", "required_vars": ["target_segment"]}],
            )
        ]
        mock_engine = unittest.mock.MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        with unittest.mock.patch.object(store, "_engine", return_value=mock_engine):
            store.refresh()
            template = store.get("campaign.plan.instructions")
            self.assertEqual(template.version, 1)
            self.assertEqual(template.body, "Plan campaign body")

    def test_history_parses_json_revisions(self):
        store = PgPromptStore()
        mock_conn = unittest.mock.MagicMock()
        mock_conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "prompt_versions": [
                {"version": 1, "body": "v1 body", "created_at": "2026-09-01T00:00:00Z", "created_by": "seed", "note": "init"},
                {"version": 2, "body": "v2 body", "created_at": "2026-09-02T00:00:00Z", "created_by": "admin", "note": "update"},
            ]
        }
        mock_engine = unittest.mock.MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        with unittest.mock.patch.object(store, "_engine", return_value=mock_engine):
            hist = store.history("test.key")
            self.assertEqual(len(hist), 2)
            self.assertEqual(hist[0]["version"], 2)
            self.assertEqual(hist[1]["version"], 1)

    def test_history_negative_limit_raises(self):
        store = PgPromptStore()
        with self.assertRaises(ValueError):
            store.history("test.key", limit=-1)

    def test_rollback_raises_on_missing_version(self):
        store = PgPromptStore()
        mock_conn = unittest.mock.MagicMock()
        mock_conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "agent_code": "agent_test",
            "prompt_engine": "none",
            "prompt_versions": [{"version": 1, "body": "v1"}],
        }
        mock_engine = unittest.mock.MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        with unittest.mock.patch.object(store, "_engine", return_value=mock_engine):
            with self.assertRaises(PromptNotFound):
                store.rollback("test.key", version=99)

    def test_publish_first_version_inserts_new_agent(self):
        store = PgPromptStore()
        mock_conn = unittest.mock.MagicMock()
        mock_conn.execute.return_value.scalar_one_or_none.return_value = "prompt_12345"
        mock_engine = unittest.mock.MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        with unittest.mock.patch.object(store, "_engine", return_value=mock_engine), \
             unittest.mock.patch.object(store, "refresh") as mock_refresh:
            ver = store.publish("brand.new.key", "Hello world instruction", note="initial")
            self.assertEqual(ver, 1)
            mock_refresh.assert_called_once()
            self.assertTrue(mock_conn.execute.called)

    def test_publish_subsequent_version_appends_and_increments(self):
        store = PgPromptStore()
        mock_conn = unittest.mock.MagicMock()
        mock_conn.execute.return_value.scalar_one_or_none.return_value = None
        mock_conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "agent_code": "agent_test",
            "instruction_version": 1,
            "system_instructions": "v1 instruction",
            "required_variables": [],
            "prompt_versions": [{"version": 1, "body": "v1 instruction", "required_vars": []}],
        }
        mock_engine = unittest.mock.MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        with unittest.mock.patch.object(store, "_engine", return_value=mock_engine), \
             unittest.mock.patch.object(store, "refresh") as mock_refresh:
            ver = store.publish("brand.new.key", "v2 instruction", note="revision 2")
            self.assertEqual(ver, 2)
            mock_refresh.assert_called_once()

    def test_rollback_success_reverts_instruction_version(self):
        store = PgPromptStore()
        mock_conn = unittest.mock.MagicMock()
        mock_conn.execute.return_value.mappings.return_value.one_or_none.return_value = {
            "agent_code": "agent_test",
            "prompt_engine": "none",
            "prompt_versions": [
                {"version": 1, "body": "v1 body", "required_vars": []},
                {"version": 2, "body": "v2 body", "required_vars": []},
            ],
        }
        mock_engine = unittest.mock.MagicMock()
        mock_engine.begin.return_value.__enter__.return_value = mock_conn
        with unittest.mock.patch.object(store, "_engine", return_value=mock_engine), \
             unittest.mock.patch.object(store, "refresh") as mock_refresh:
            ver = store.rollback("brand.new.key", version=1)
            self.assertEqual(ver, 1)
            mock_refresh.assert_called_once()


class GetStoreTests(unittest.TestCase):
    def test_singleton_and_reset(self):
        reset_store_cache()
        s1 = get_store()
        self.assertIs(s1, get_store())
        reset_store_cache()
        self.assertIsNot(s1, get_store())


if __name__ == "__main__":
    unittest.main()
