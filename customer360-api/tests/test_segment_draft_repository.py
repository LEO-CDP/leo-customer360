"""Unit tests for core.repositories.segment_draft_repository.

No PostgreSQL, no model: SegmentRepository and generate_segment_rules are patched.
"""

import unittest
import uuid
from unittest.mock import patch

from leo_customer360_agent.client import AIProviderError, GeneratedSegmentRules

from core.repositories.segment_draft_repository import (
    DESCRIPTION_MAX_LEN,
    SegmentDraftGenerationError,
    SegmentDraftRepository,
    SegmentDraftValidationError,
    _normalize_state_text,
    parse_allowed_values,
)

MODULE = "core.repositories.segment_draft_repository"
TENANT = uuid.uuid4()

# Captured from PostgreSQL 16 pg_get_constraintdef() for the same CHECKs as database-schema.sql.
PG_CHECK_DEFS = [
    "CHECK (((is_hashed = false) OR (persona_name IS NOT NULL)))",
    "CHECK ((gender = ANY (ARRAY['male'::text, 'female'::text, 'other'::text])))",
    "CHECK (((latest_nps_score >= 0) AND (latest_nps_score <= 10)))",
    "CHECK ((lifecycle_stage = ANY (ARRAY['prospect'::text, 'lead'::text, 'customer'::text, "
    "'vip'::text, 'dormant'::text, 'churn_risk'::text])))",
    "CHECK ((weird = ANY (ARRAY['it''s'::text, 'a b'::text])))",
]

CATALOG = [
    {"field": "gender", "name": "Gender", "data_type": "TEXT", "description": "male, female, or other.",
     "is_pii": True, "source_table": "cdp_master_profiles", "domain_scope": "all", "attribute_group": "IDENTITY"},
    {"field": "customer_since", "name": "Customer Since", "data_type": "DATE", "description": "First paid",
     "is_pii": False, "source_table": "cdp_master_profiles", "domain_scope": "all", "attribute_group": "LIFECYCLE"},
    {"field": "city", "name": "City", "data_type": "TEXT", "description": "Billing city",
     "is_pii": False, "source_table": "cdp_master_profiles", "domain_scope": "all", "attribute_group": "GENERAL"},
    {"field": "preferred_channels", "name": "Channels", "data_type": "ARRAY",
     "is_pii": False, "source_table": "cdp_master_profiles", "domain_scope": "all", "attribute_group": "GENERAL"},
]


def _rules(rules, tag="Male Since Sep", name="Male Customers Since 2026-09-01", explanation="ok",
           outcome=None, so_far=None):
    return GeneratedSegmentRules(
        segment_tag=tag, segment_name=name,
        json_rules={"condition": "AND", "rules": rules}, explanation=explanation,
        outcome=outcome or ("rules" if rules else "ask"),
        so_far=so_far,
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, check_defs=PG_CHECK_DEFS, known=None, text_columns=("gender", "city")):
        self.commits = 0
        self.check_defs = check_defs
        self.known = known or {}  # column -> distinct values
        self.text_columns = text_columns
        self.statements = []

    def execute(self, stmt, params=None):
        self.statements.append((str(stmt), params))
        sql = str(stmt)
        if sql.startswith("SELECT DISTINCT"):
            return _Result(self.known.get(sql.split('"')[1], []))
        if "information_schema" in sql:
            return _Result(self.text_columns)
        return _Result(self.check_defs)

    def commit(self):
        self.commits += 1


class _Repo:
    def __init__(self, catalog):
        self._catalog = catalog
        self.domains = []

    def __call__(self, session):
        return self

    def get_segmentable_attributes(self, domain=None):
        self.domains.append(domain)
        return [dict(r) for r in self._catalog]


class ParseAllowedValuesTests(unittest.TestCase):
    def test_reads_single_column_in_lists_and_ignores_other_checks(self):
        self.assertEqual(parse_allowed_values(PG_CHECK_DEFS), {
            "gender": ["male", "female", "other"],
            "lifecycle_stage": ["prospect", "lead", "customer", "vip", "dormant", "churn_risk"],
            "weird": ["it's", "a b"],
        })

    def test_garbage_is_ignored(self):
        self.assertEqual(parse_allowed_values(["", None, "CHECK (x)", "NOT A CHECK"]), {})


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.session = _FakeSession()
        self.repo = SegmentDraftRepository(self.session)

    def _run(self, generated, catalog=CATALOG, description="gender is make, customer_since 2026-09-01",
             domain="all", so_far=None, last_question=None):
        fake_repo = _Repo(catalog)
        with patch(f"{MODULE}.SegmentRepository", fake_repo), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            if isinstance(generated, Exception):
                gen.side_effect = generated
            else:
                gen.return_value = generated
            out = self.repo.draft_from_description(
                TENANT, description, domain=domain, so_far=so_far, last_question=last_question)
        return out, gen, fake_repo

    # ------------------------------------------------------------- spec test case 1
    def test_valid_rules_come_back_in_the_form_shape_with_no_sql(self):
        out, _, fake_repo = self._run(_rules([
            {"field": "gender", "operator": "equal", "value": "male"},
            {"field": "customer_since", "operator": "equal", "value": "2026-09-01"},
        ], explanation="Interpreted 'make' as 'male'."))
        self.assertEqual(out.validation_status, "valid")
        self.assertTrue(out.ready_for_segment_persistence)
        self.assertEqual([r["id"] for r in out.json_rules["rules"]], ["gender", "customer_since"])
        self.assertEqual(out.json_rules["rules"][1]["value"], "2026-09-01")
        self.assertIn("make", out.interpretation)
        self.assertEqual(out.segment_tag, "male_since_sep")
        self.assertEqual(out.domain, "all")
        self.assertEqual(fake_repo.domains, ["all"])
        dumped = repr(out.model_dump())
        self.assertNotIn("sql_rules", dumped)
        self.assertNotIn("SELECT", dumped.upper().replace("SELECTED", ""))

    def test_uncorrected_typo_asks_with_the_schema_choices(self):
        out, _, _ = self._run(_rules([{"field": "gender", "operator": "equal", "value": "make"}]))
        self.assertEqual(out.validation_status, "needs_clarification")
        self.assertFalse(out.ready_for_segment_persistence)
        self.assertIsNone(out.json_rules)
        self.assertEqual(out.suggestions, ["male", "female", "other"])

    # ------------------------------------------------------------- spec test case 2
    def test_model_that_declines_to_guess_is_a_question_not_an_error(self):
        out, _, _ = self._run(_rules([], tag="", name="", explanation="Did you mean male?"),
                              description="gender is m")
        self.assertEqual(out.validation_status, "needs_clarification")
        self.assertEqual(out.question, "Did you mean male?")

    # ------------------------------------------------------------- spec test case 3
    def test_other_table_is_rejected_with_a_reason(self):
        out, _, _ = self._run(_rules([{"field": "crm_transactions.amount", "operator": "greater", "value": 100}]))
        self.assertEqual(out.validation_status, "rejected")
        self.assertIn("customer-profile attributes", out.question)

    def test_model_not_possible_is_rejected_with_its_reason(self):
        out, _, _ = self._run(_rules([], tag="", name="", explanation="Purchases are not profile attributes.",
                                     outcome="not_possible"), description="customers who bought an iPhone")
        self.assertEqual(out.validation_status, "rejected")
        self.assertEqual(out.question, "Purchases are not profile attributes.")

    def test_sql_in_the_description_is_rejected_without_calling_the_model(self):
        with patch(f"{MODULE}.SegmentRepository", _Repo(CATALOG)), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            out = self.repo.draft_from_description(
                TENANT, "profiles where lead_grade = 'A'; DROP TABLE customer360.cdp_master_profiles; --")
        gen.assert_not_called()
        self.assertEqual(out.validation_status, "rejected")
        self.assertIn("SQL", out.question)

    # ------------------------------------------------------------- multi-turn
    def test_valid_builder_rules_reach_the_model_as_field_operator_value(self):
        builder = {"condition": "AND", "rules": [{"id": "city", "field": "city", "type": "string",
                                                  "input": "text", "operator": "equal", "value": "Hue"}]}
        fake_repo = _Repo(CATALOG)
        with patch(f"{MODULE}.SegmentRepository", fake_repo), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            gen.return_value = _rules([{"field": "gender", "operator": "equal", "value": "male"}])
            out = self.repo.draft_from_description(TENANT, "male", current_rules=builder,
                                                   last_question="Which did you mean: male, female, other?")
        brief = gen.call_args.args[0]
        # The model sees the builder tree as field/operator/value only.
        self.assertEqual(brief.current_rules, {"condition": "AND", "rules": [
            {"field": "city", "operator": "equal", "value": "Hue"}]})
        self.assertEqual(brief.last_question, "Which did you mean: male, female, other?")
        self.assertFalse(hasattr(brief, "history"))
        self.assertEqual(out.validation_status, "valid")

    def test_invalid_builder_rules_are_not_shown_to_the_model(self):
        with patch(f"{MODULE}.SegmentRepository", _Repo(CATALOG)), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            gen.return_value = _rules([{"field": "city", "operator": "equal", "value": "Hue"}])
            self.repo.draft_from_description(TENANT, "also in Hue", current_rules={
                "condition": "AND", "rules": [{"field": "tenant_id", "operator": "equal", "value": "x"}]})
        self.assertIsNone(gen.call_args.args[0].current_rules)

    def test_sql_in_a_value_is_rejected(self):
        out, _, _ = self._run(_rules([{"field": "city", "operator": "equal", "value": "1=1; DROP TABLE x"}]))
        self.assertEqual(out.validation_status, "rejected")
        self.assertIsNone(out.json_rules)

    # ------------------------------------------------------------- transaction boundary
    def test_read_transaction_is_committed_before_the_model_call(self):
        seen = []

        def _generate(brief):
            seen.append(self.session.commits)
            return _rules([{"field": "city", "operator": "equal", "value": "Hanoi"}])

        with patch(f"{MODULE}.SegmentRepository", _Repo(CATALOG)), \
             patch(f"{MODULE}.generate_segment_rules", side_effect=_generate):
            self.repo.draft_from_description(TENANT, "customers in Hanoi")
        self.assertEqual(seen, [1])

    def test_nothing_is_written(self):
        self._run(_rules([{"field": "city", "operator": "equal", "value": "Hanoi"}]))
        for sql, _ in self.session.statements:
            self.assertTrue(sql.strip().upper().startswith("SELECT"), sql)

    # ------------------------------------------------------------- what the model sees
    def test_model_sees_form_hints_but_no_internals(self):
        _, gen, _ = self._run(_rules([{"field": "city", "operator": "equal", "value": "Hanoi"}]))
        attrs = {a["field"]: a for a in gen.call_args.args[0].attributes}
        self.assertEqual(attrs["gender"]["allowed_values"], ["male", "female", "other"])
        self.assertNotIn("greater", attrs["city"]["operators"])
        self.assertIn("date picker", attrs["customer_since"]["value_format"])
        self.assertNotIn("preferred_channels", attrs)     # arrays are not offered by the form
        sent = repr(gen.call_args.args[0].attributes)
        for internal in ("is_pii", "source_table", "cdp_master_profiles", "domain_scope", "attribute_group"):
            self.assertNotIn(internal, sent)

    # ------------------------------------------------------------- language
    def test_vietnamese_input_gets_vietnamese_fixed_messages(self):
        out, gen, _ = self._run(_rules([]), description="khách VIP; DROP TABLE cdp_master_profiles")
        self.assertIn("giống SQL", out.question)
        gen.assert_not_called()
        out, _, _ = self._run(_rules([{"field": "gender", "operator": "equal", "value": "m"}]),
                              description="giới tính là m")
        self.assertTrue(out.question.startswith("Với “"))
        out, _, _ = self._run(_rules([{"field": "gender", "operator": "equal", "value": "m"}]),
                              description="gender is m")
        self.assertTrue(out.question.startswith("For “"))

    # ------------------------------------------------------------- known values
    def test_value_case_is_fixed_to_the_tenants_stored_value(self):
        self.session.known = {"city": ["Hà Nội", "Đà Nẵng"]}
        out, gen, _ = self._run(_rules([{"field": "city", "operator": "equal", "value": "hà nội"}]))
        self.assertEqual(out.json_rules["rules"][0]["value"], "Hà Nội")
        self.assertNotIn("Đà Nẵng", repr(gen.call_args.args[0].attributes))  # never sent to the model

    def test_known_values_are_tenant_filtered_and_skip_pii_and_listed_columns(self):
        self._run(_rules([{"field": "city", "operator": "equal", "value": "Hue"}]))
        distinct = [(sql, p) for sql, p in self.session.statements if sql.startswith("SELECT DISTINCT")]
        self.assertEqual(len(distinct), 1)              # gender: PII and has a CHECK list
        sql, params = distinct[0]
        self.assertIn('"city"', sql)
        self.assertIn("tenant_id = :tenant_id", sql)
        self.assertEqual(params["tenant_id"], str(TENANT))

    def test_catalog_fields_that_are_not_text_columns_are_not_queried(self):
        self.session.text_columns = ("gender",)
        self._run(_rules([{"field": "city", "operator": "equal", "value": "Hue"}]))
        self.assertFalse(any(sql.startswith("SELECT DISTINCT") for sql, _ in self.session.statements))

    def test_high_cardinality_columns_get_no_known_values(self):
        self.session.known = {"city": [f"c{i}" for i in range(21)]}
        out, _, _ = self._run(_rules([{"field": "city", "operator": "equal", "value": "C1"}]))
        self.assertEqual(out.json_rules["rules"][0]["value"], "C1")

    def test_database_catalog_is_the_authority(self):
        out, _, _ = self._run(_rules([{"field": "loyalty_tier", "operator": "equal", "value": "gold"}]))
        self.assertEqual(out.validation_status, "rejected")

    # ------------------------------------------------------------- failures
    def test_agent_failure_becomes_a_generation_error(self):
        with self.assertRaises(SegmentDraftGenerationError):
            self._run(AIProviderError("agent unreachable"))

    def _assert_refused_without_calling_model(self, tenant=TENANT, description="customers in Hanoi", catalog=CATALOG):
        with patch(f"{MODULE}.SegmentRepository", _Repo(catalog)), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            with self.assertRaises(SegmentDraftValidationError):
                self.repo.draft_from_description(tenant, description)
        gen.assert_not_called()

    def test_input_refusals_happen_before_the_model(self):
        self._assert_refused_without_calling_model(description="   ")
        self._assert_refused_without_calling_model(description="x" * 2001)
        self._assert_refused_without_calling_model(catalog=[])
        self._assert_refused_without_calling_model(catalog=[CATALOG[3]])          # only arrays
        # Guards against a caller passing a tenant string lifted from the request body.
        self._assert_refused_without_calling_model(tenant=str(TENANT))


class NormalizeStateTextTests(unittest.TestCase):
    def test_collapses_whitespace_and_caps_length(self):
        self.assertEqual(_normalize_state_text("  wants   VIPs  \n in Hue "), "wants VIPs in Hue")
        self.assertEqual(_normalize_state_text("a" * 3000), "a" * DESCRIPTION_MAX_LEN)

    def test_blank_or_missing_becomes_none(self):
        self.assertIsNone(_normalize_state_text(None))
        self.assertIsNone(_normalize_state_text(""))
        self.assertIsNone(_normalize_state_text("   \n  "))


class SoFarStateTests(unittest.TestCase):
    """so_far/last_question reach the brief, are SQL-checked, and round-trip per outcome."""

    def setUp(self):
        self.session = _FakeSession()
        self.repo = SegmentDraftRepository(self.session)

    def _run(self, generated, so_far=None, last_question=None, description="male",
             catalog=CATALOG):
        fake_repo = _Repo(catalog)
        with patch(f"{MODULE}.SegmentRepository", fake_repo), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            gen.return_value = generated
            out = self.repo.draft_from_description(
                TENANT, description, so_far=so_far, last_question=last_question)
        return out, gen

    def test_so_far_and_last_question_reach_the_brief(self):
        _, gen = self._run(
            _rules([{"field": "gender", "operator": "equal", "value": "male"}]),
            so_far="Wants VIP customers", last_question="Which gender?",
        )
        brief = gen.call_args.args[0]
        self.assertEqual(brief.so_far, "Wants VIP customers")
        self.assertEqual(brief.last_question, "Which gender?")

    def test_whitespace_is_normalised_before_reaching_the_brief(self):
        _, gen = self._run(
            _rules([{"field": "gender", "operator": "equal", "value": "male"}]),
            so_far="  Wants   VIPs \n ", last_question="   ",
        )
        brief = gen.call_args.args[0]
        self.assertEqual(brief.so_far, "Wants VIPs")
        self.assertIsNone(brief.last_question)     # blank collapses to None, not sent as ""

    def test_valid_result_returns_the_models_so_far(self):
        out, _ = self._run(
            _rules([{"field": "gender", "operator": "equal", "value": "male"}],
                   so_far="Wants VIP male customers"),
            so_far="Wants VIP customers",
        )
        self.assertEqual(out.validation_status, "valid")
        self.assertEqual(out.so_far, "Wants VIP male customers")

    def test_valid_result_falls_back_to_incoming_so_far_when_model_gives_none(self):
        out, _ = self._run(
            _rules([{"field": "gender", "operator": "equal", "value": "male"}], so_far=None),
            so_far="Wants VIP customers",
        )
        self.assertEqual(out.validation_status, "valid")
        self.assertEqual(out.so_far, "Wants VIP customers")

    def test_model_asking_uses_its_own_so_far_when_present(self):
        out, _ = self._run(
            _rules([], tag="", name="", explanation="Which city?",
                   so_far="Wants VIPs; also asked about city"),
            so_far="Wants VIPs", last_question=None, description="also filter by city",
        )
        self.assertEqual(out.validation_status, "needs_clarification")
        self.assertEqual(out.so_far, "Wants VIPs; also asked about city")

    def test_model_asking_falls_back_to_incoming_so_far_when_it_gives_none(self):
        out, _ = self._run(
            _rules([], tag="", name="", explanation="Which city?", so_far=None),
            so_far="Wants VIPs",
        )
        self.assertEqual(out.validation_status, "needs_clarification")
        self.assertEqual(out.so_far, "Wants VIPs")

    def test_not_possible_keeps_incoming_so_far_even_if_model_returns_one(self):
        out, _ = self._run(
            _rules([], tag="", name="", explanation="Purchases are not profile attributes.",
                   outcome="not_possible", so_far="model's own summary"),
            so_far="Wants VIPs", description="customers who bought an iPhone",
        )
        self.assertEqual(out.validation_status, "rejected")
        self.assertEqual(out.so_far, "Wants VIPs")

    def test_unresolved_value_keeps_incoming_so_far(self):
        out, _ = self._run(
            _rules([{"field": "gender", "operator": "equal", "value": "make"}]),
            so_far="Wants VIPs",
        )
        self.assertEqual(out.validation_status, "needs_clarification")
        self.assertEqual(out.so_far, "Wants VIPs")

    def test_rule_validation_error_keeps_incoming_so_far(self):
        out, _ = self._run(
            _rules([{"field": "crm_transactions.amount", "operator": "greater", "value": 100}]),
            so_far="Wants VIPs",
        )
        self.assertEqual(out.validation_status, "rejected")
        self.assertEqual(out.so_far, "Wants VIPs")

    def test_sql_hidden_in_so_far_is_rejected_without_calling_the_model(self):
        with patch(f"{MODULE}.SegmentRepository", _Repo(CATALOG)), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            out = self.repo.draft_from_description(
                TENANT, "VIP customers",
                so_far="x'; DROP TABLE customer360.cdp_master_profiles; --",
            )
        gen.assert_not_called()
        self.assertEqual(out.validation_status, "rejected")
        self.assertIn("SQL", out.question)

    def test_sql_hidden_in_last_question_is_rejected_without_calling_the_model(self):
        with patch(f"{MODULE}.SegmentRepository", _Repo(CATALOG)), \
             patch(f"{MODULE}.generate_segment_rules") as gen:
            out = self.repo.draft_from_description(
                TENANT, "VIP customers",
                last_question="1=1; DROP TABLE customer360.cdp_master_profiles; --",
            )
        gen.assert_not_called()
        self.assertEqual(out.validation_status, "rejected")


if __name__ == "__main__":
    unittest.main()
