"""Prompt-store tests: template rendering, the validate rail, and the Pg store's
snapshot read path (bodies live in the DB, seeded by database-init; no in-code
fallback -- so these exercise logic without a live DB)."""

import unittest

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


class GetStoreTests(unittest.TestCase):
    def test_singleton_and_reset(self):
        reset_store_cache()
        s1 = get_store()
        self.assertIs(s1, get_store())
        reset_store_cache()
        self.assertIsNot(s1, get_store())


if __name__ == "__main__":
    unittest.main()
