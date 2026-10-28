"""Unit tests for the screen-facts registry and its shared formatters."""

import unittest
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import InternalError, ProgrammingError

from sqlalchemy.exc import ProgrammingError

from core.repositories import screen_facts
from core.repositories.campaign_draft_repository import CampaignDraftNotFoundError
from core.repositories.screen_facts import (
    ANALYTICS_DEFAULT_DAYS,
    ANALYTICS_PAGE,
    CAMPAIGN_COUNT_LINES_MAX,
    CAMPAIGN_EDIT_PAGE,
    CAMPAIGN_EXPERIMENTS_MAX,
    CAMPAIGN_PAGE,
    CAMPAIGN_VARIANTS_MAX,
    DAILY_TOTALS_MAX,
    OVERVIEW_DEFAULT_DAYS,
    OVERVIEW_PAGE,
    PERIOD_MAX_DAYS,
    PERSONA_PAGE,
    PROFILE_PAGE,
    SCREEN_FACTS,
    SEGMENT_PAGE,
    SEGMENT_RULES_MAX,
    SEGMENT_RULES_MAX_CHARS,
    SOURCE_SYSTEM_TOP,
    STAFF_TEXT_MAX_CHARS,
    ScreenFacts,
    build_campaign_facts,
    build_persona_facts,
    build_segment_facts,
    fact,
    field_names,
    load_analytics_facts,
    load_campaign_facts,
    load_overview_facts,
    load_persona_facts,
    load_profile_facts,
    load_segment_facts,
    staff_text,
    summarize_segment_rules,
)

TENANT = uuid.uuid4()
PROFILE = uuid.uuid4()
SEGMENT = uuid.uuid4()
CAMPAIGN = uuid.uuid4()
PERSONA = uuid.uuid4()
FACTS = [
    "Churn risk tier (churn_risk_tier): high — Bucketized churn risk",
    "Next best action: call back",
]


class StaffTextTests(unittest.TestCase):
    def test_an_email_is_masked_and_the_text_is_marked_staff_written(self):
        line = staff_text("Segment name", "Linh's segment linh@example.com")
        self.assertEqual(line, 'Segment name (written by staff): "Linh\'s segment [email]"')

    def test_whitespace_collapses(self):
        self.assertEqual(staff_text("Name", "  A\n\n  B\tC "), 'Name (written by staff): "A B C"')

    def test_the_value_is_capped_at_two_hundred_characters(self):
        line = staff_text("Name", "x" * 500)
        quoted = line.split('"')[1]
        self.assertEqual(len(quoted), STAFF_TEXT_MAX_CHARS)
        self.assertEqual(len(line), len('Name (written by staff): ""') + STAFF_TEXT_MAX_CHARS)

    def test_blank_values_become_none(self):
        for value in (None, "", "   ", "\n\t"):
            self.assertIsNone(staff_text("Name", value), repr(value))


class FactTests(unittest.TestCase):
    def test_values_are_rendered_with_the_shared_show_formatter(self):
        self.assertEqual(fact("Active", True), "Active: yes")
        self.assertEqual(fact("Count", Decimal("12.5")), "Count: 12.50")
        self.assertEqual(fact("Since", date(2026, 1, 2)), "Since: 2026-01-02")

    def test_an_empty_value_becomes_none(self):
        for value in (None, "", []):
            self.assertIsNone(fact("Active", value), repr(value))


class FieldNamesTests(unittest.TestCase):
    def test_names_are_taken_before_the_parenthesis_and_colon(self):
        lines = [
            "Churn risk tier (churn_risk_tier): high",
            "Next best action: call back",
            "Persona: not computed yet for this customer, so there is no Next Best Action",
        ]
        self.assertEqual(field_names(lines), ["Churn risk tier", "Next best action", "Persona"])


class ProfileLoaderTests(unittest.TestCase):
    def test_the_profile_page_is_registered(self):
        self.assertIs(SCREEN_FACTS[PROFILE_PAGE], load_profile_facts)

    def test_the_loader_wraps_todays_facts_unchanged(self):
        db = MagicMock()
        with patch.object(screen_facts.AssistantRepository, "profile_facts", return_value=FACTS) as load:
            loaded = load_profile_facts(db, TENANT, PROFILE, None)

        load.assert_called_once_with(TENANT, PROFILE)
        self.assertIsInstance(loaded, ScreenFacts)
        self.assertEqual(loaded.title, "Customer profile on screen")
        self.assertEqual(loaded.label, "this customer's profile")
        self.assertEqual(loaded.lines, FACTS)
        self.assertEqual(loaded.fields, ["Churn risk tier", "Next best action"])

    def test_the_loader_ignores_the_period(self):
        db = MagicMock()
        with patch.object(screen_facts.AssistantRepository, "profile_facts", return_value=FACTS) as load:
            load_profile_facts(db, TENANT, PROFILE, 30)
        load.assert_called_once_with(TENANT, PROFILE)

    def test_a_missing_profile_is_none(self):
        db = MagicMock()
        with patch.object(screen_facts.AssistantRepository, "profile_facts", return_value=None):
            self.assertIsNone(load_profile_facts(db, TENANT, PROFILE, None))

    def test_no_entity_id_is_none_without_touching_the_repository(self):
        db = MagicMock()
        with patch.object(screen_facts.AssistantRepository, "profile_facts") as load:
            self.assertIsNone(load_profile_facts(db, TENANT, None, None))
        load.assert_not_called()


class SegmentRuleSummaryTests(unittest.TestCase):
    CATALOG = {
        "age": ("Age", False),
        "city": ("City", False),
        "national_id": ("National ID", True),
    }

    def test_a_non_pii_value_is_shown_and_the_operator_is_words(self):
        rules = {"condition": "AND", "rules": [{"field": "age", "operator": "greater_or_equal", "value": 18}]}
        self.assertEqual(summarize_segment_rules(rules, self.CATALOG), "Age is at least 18")

    def test_a_pii_value_is_hidden(self):
        rules = {"condition": "AND", "rules": [{"field": "national_id", "operator": "equal", "value": "123456789"}]}
        self.assertEqual(summarize_segment_rules(rules, self.CATALOG), "National ID is (value hidden)")

    def test_a_field_missing_from_the_catalog_is_hidden(self):
        rules = {"condition": "AND", "rules": [{"field": "unknown_field", "operator": "equal", "value": "secret"}]}
        self.assertEqual(summarize_segment_rules(rules, self.CATALOG), "unknown_field is (value hidden)")

    def test_and_or_groups_keep_their_parentheses(self):
        rules = {
            "condition": "AND",
            "rules": [
                {"field": "age", "operator": "greater_or_equal", "value": 18},
                {
                    "condition": "OR",
                    "rules": [
                        {"field": "city", "operator": "equal", "value": "Hanoi"},
                        {"field": "city", "operator": "equal", "value": "HCM"},
                    ],
                },
            ],
        }
        self.assertEqual(
            summarize_segment_rules(rules, self.CATALOG),
            "Age is at least 18 AND (City is Hanoi OR City is HCM)",
        )

    def test_an_operator_without_a_value_has_no_value(self):
        rules = {"condition": "AND", "rules": [{"field": "city", "operator": "is_null"}]}
        self.assertEqual(summarize_segment_rules(rules, self.CATALOG), "City is empty")

    def test_the_rule_count_is_capped_at_twenty(self):
        rules = {
            "condition": "AND",
            "rules": [{"field": "age", "operator": "equal", "value": index} for index in range(30)],
        }
        summary = summarize_segment_rules(rules, self.CATALOG)
        self.assertEqual(summary.count("Age is"), SEGMENT_RULES_MAX)

    def test_the_summary_is_capped_at_fifteen_hundred_characters(self):
        rules = {
            "condition": "AND",
            "rules": [{"field": "city", "operator": "equal", "value": "x" * 200} for _ in range(20)],
        }
        self.assertEqual(len(summarize_segment_rules(rules, self.CATALOG)), SEGMENT_RULES_MAX_CHARS)

    def test_empty_or_malformed_rules_are_none(self):
        for rules in (None, {}, {"condition": "AND", "rules": []}, "not-a-tree", {"rules": "nope"}):
            self.assertIsNone(summarize_segment_rules(rules, self.CATALOG), repr(rules))


class SegmentFactsTests(unittest.TestCase):
    def _segment(self, **extra):
        values = {
            "segment_id": SEGMENT,
            "tenant_id": TENANT,
            "segment_name": "High value",
            "description": "Customers worth a call",
            "is_active": True,
            "processed_by": "ai_agent",
            "domain": "retail",
            "member_count": 42,
            "last_computed_at": datetime(2026, 10, 1, 8, 30),
            "created_at": datetime(2026, 9, 1, 8, 0),
            "updated_at": datetime(2026, 9, 2, 8, 0),
            "status_code": 1,
            "json_rules": {"condition": "AND", "rules": [{"field": "age", "operator": "greater_or_equal", "value": 18}]},
            # everything below must never reach the prompt
            "sql_rules": "ZZSQL age >= 18",
            "final_generated_sql": "ZZFINAL SELECT ...",
            "user_id": "ZZUSER",
            "matched_profiles": ["ZZMATCHED"],
        }
        values.update(extra)
        return SimpleNamespace(**values)

    def test_only_allowlisted_fields_are_sent(self):
        lines = build_segment_facts(self._segment(), {"age": ("Age", False)}, [])
        text = "\n".join(lines)

        self.assertIn('Name (written by staff): "High value"', lines)
        self.assertIn('Description (written by staff): "Customers worth a call"', lines)
        self.assertIn("Active: yes", lines)
        self.assertIn("Processed by: AI", lines)
        self.assertIn("Domain: retail", lines)
        self.assertIn("Audience size: 42", lines)
        self.assertIn("Last computed: 2026-10-01", lines)
        self.assertIn("Created: 2026-09-01", lines)
        self.assertIn("Updated: 2026-09-02", lines)
        self.assertIn("Status: Active", lines)
        self.assertIn("Rules: Age is at least 18", lines)
        for sentinel in ("ZZSQL", "ZZFINAL", "ZZUSER", "ZZMATCHED"):
            self.assertNotIn(sentinel, text)

    def test_staff_text_is_masked_and_capped(self):
        lines = build_segment_facts(
            self._segment(segment_name="Linh's segment linh@example.com", description="x" * 500),
            {},
            [],
        )
        self.assertIn('Name (written by staff): "Linh\'s segment [email]"', lines)
        description = next(line for line in lines if line.startswith("Description"))
        self.assertEqual(len(description.split('"')[1]), STAFF_TEXT_MAX_CHARS)

    def test_workflow_steps_are_included(self):
        lines = build_segment_facts(
            self._segment(),
            {},
            [("churn_agent", 1, True, "daily"), ("clv_agent", 2, False, None)],
        )
        self.assertIn("Agent workflow: step 1, churn_agent, active, schedule: daily", lines)
        self.assertIn("Agent workflow: step 2, clv_agent, inactive", lines)

    def test_the_loader_returns_none_for_another_tenant(self):
        db = MagicMock()
        db.get.return_value = self._segment(tenant_id=uuid.uuid4())
        self.assertIsNone(load_segment_facts(db, TENANT, SEGMENT, None))
        db.execute.assert_not_called()

    def test_the_loader_returns_none_without_an_id(self):
        db = MagicMock()
        self.assertIsNone(load_segment_facts(db, TENANT, None, None))
        db.get.assert_not_called()

    def test_the_loader_tolerates_a_missing_workflow_table(self):
        db = MagicMock()
        db.get.return_value = self._segment()
        db.execute.side_effect = ProgrammingError("SELECT", {}, Exception("relation does not exist"))
        with patch.object(screen_facts, "_segment_rule_catalog", return_value={"age": ("Age", False)}):
            loaded = load_segment_facts(db, TENANT, SEGMENT, None)

        self.assertIsInstance(loaded, ScreenFacts)
        self.assertEqual(loaded.title, "Segment on screen")
        self.assertEqual(loaded.label, "this segment's details")
        self.assertIn("Rules: Age is at least 18", loaded.lines)

    def test_the_loader_builds_the_facts_from_the_tenant_checked_segment(self):
        db = MagicMock()
        db.get.return_value = self._segment()
        with patch.object(screen_facts, "_segment_rule_catalog", return_value={"age": ("Age", False)}) as catalog, patch.object(
            screen_facts, "_segment_workflow_steps", return_value=[("churn_agent", 1, True, None)]
        ) as workflow:
            loaded = load_segment_facts(db, TENANT, SEGMENT, 30)

        db.get.assert_called_once()
        catalog.assert_called_once()
        workflow.assert_called_once_with(db, TENANT, SEGMENT)
        self.assertEqual(loaded.fields[0], "Name")
        self.assertIn("Agent workflow", "\n".join(loaded.lines))


class CampaignFactsTests(unittest.TestCase):
    def _campaign(self, **extra):
        values = {
            "campaign_id": CAMPAIGN,
            "tenant_id": TENANT,
            "name": "Autumn push",
            "description": "Win back lapsed buyers",
            "status": "Active",
            "approval_status": "Approved",
            "channel": "email",
            "platform": "sendgrid",
            "objective": "conversion",
            "lang": "en",
            "currency": "VND",
            "start_date": date(2026, 10, 1),
            "end_date": date(2026, 10, 31),
            "budget_amount": Decimal("1000.00"),
            "approved_at": datetime(2026, 9, 30, 12, 0),
            "segment_id": SEGMENT,
            # everything below must never reach the prompt
            "keywords": ["ZZKEYWORD"],
            "strategy_summary": "ZZSTRATEGY",
            "ai_plan": {"secret": "ZZPLAN"},
            "user_id": "ZZUSER",
            "approved_by": "ZZAPPROVER",
        }
        values.update(extra)
        return SimpleNamespace(**values)

    def _segment(self):
        return SimpleNamespace(segment_name="High value", member_count=42, is_active=True, tenant_id=TENANT)

    def _metrics(self):
        return SimpleNamespace(
            total_spend=Decimal("500.00"),
            total_impressions=1000,
            total_clicks=100,
            total_conversions=10,
            total_revenue=Decimal("2000.00"),
            ctr_percentage=Decimal("10.00"),
            cvr_percentage=Decimal("10.00"),
            cpa=Decimal("50.00"),
            roas=Decimal("4.00"),
        )

    def test_only_allowlisted_fields_are_sent(self):
        lines = build_campaign_facts(self._campaign(), self._segment(), self._metrics(), [], {}, {})
        text = "\n".join(lines)

        self.assertIn('Name (written by staff): "Autumn push"', lines)
        self.assertIn('Description (written by staff): "Win back lapsed buyers"', lines)
        self.assertIn("Status: Active", lines)
        self.assertIn("Approval status: Approved", lines)
        self.assertIn("Channel: email", lines)
        self.assertIn("Platform: sendgrid", lines)
        self.assertIn("Objective: conversion", lines)
        self.assertIn("Language: en", lines)
        self.assertIn("Currency: VND", lines)
        self.assertIn("Start date: 2026-10-01", lines)
        self.assertIn("End date: 2026-10-31", lines)
        self.assertIn("Budget: 1000.00", lines)
        self.assertIn("Approved at: 2026-09-30", lines)
        self.assertIn('Linked segment (written by staff): "High value"', lines)
        self.assertIn("Linked segment size: 42", lines)
        self.assertIn("Linked segment active: yes", lines)
        self.assertIn("Lifetime spend: 500.00", lines)
        self.assertIn("Lifetime impressions: 1000", lines)
        self.assertIn("Lifetime clicks: 100", lines)
        self.assertIn("Lifetime conversions: 10", lines)
        self.assertIn("Lifetime revenue: 2000.00", lines)
        self.assertIn("CTR: 10.00", lines)
        self.assertIn("CVR: 10.00", lines)
        self.assertIn("CPA: 50.00", lines)
        self.assertIn("ROAS: 4.00", lines)
        for sentinel in ("ZZKEYWORD", "ZZSTRATEGY", "ZZPLAN", "ZZUSER", "ZZAPPROVER"):
            self.assertNotIn(sentinel, text)

    def test_staff_text_is_masked_and_capped(self):
        lines = build_campaign_facts(
            self._campaign(name="Linh's push linh@example.com", description="x" * 500),
            None,
            None,
            [],
            {},
            {},
        )
        self.assertIn('Name (written by staff): "Linh\'s push [email]"', lines)
        description = next(line for line in lines if line.startswith("Description"))
        self.assertEqual(len(description.split('"')[1]), STAFF_TEXT_MAX_CHARS)

    def test_no_segment_and_no_metrics_are_said_explicitly(self):
        lines = build_campaign_facts(self._campaign(segment_id=None), None, None, [], {}, {})
        self.assertIn("Linked segment: none", lines)
        self.assertIn("Lifetime metrics: none recorded", lines)

    def test_experiments_are_counted_without_names(self):
        experiments = [
            {
                "status": "Running",
                "primary_metric": "conversions",
                "variants": [
                    {"allocation": Decimal("50.00"), "is_control": True, "conversions": 10, "cvr": Decimal("5.00"), "roas": Decimal("2.50")},
                    {"allocation": Decimal("50.00"), "is_control": False, "conversions": 20, "cvr": Decimal("8.00"), "roas": Decimal("3.00")},
                ],
            }
        ]
        lines = build_campaign_facts(self._campaign(), self._segment(), self._metrics(), experiments, {}, {})
        text = "\n".join(lines)

        self.assertIn("Experiments: 1", lines)
        self.assertIn("Experiment 1 status: Running", lines)
        self.assertIn("Experiment 1 primary metric: conversions", lines)
        self.assertIn("Experiment 1 variants: 2", lines)
        self.assertIn("Experiment 1 allocation: 50.00 / 50.00", lines)
        self.assertIn("Experiment 1 control: yes", lines)
        self.assertIn("Experiment 1 variant 1 conversions: 10", lines)
        self.assertIn("Experiment 1 variant 1 CVR: 5.00", lines)
        self.assertIn("Experiment 1 variant 1 ROAS: 2.50", lines)
        self.assertIn("Experiment 1 variant 2 conversions: 20", lines)
        self.assertNotIn("Control", text)
        self.assertNotIn("Variant A", text)

    def test_content_and_dispatch_counts_are_by_type_and_status(self):
        lines = build_campaign_facts(
            self._campaign(),
            self._segment(),
            self._metrics(),
            [],
            {"article": 2, "video": 1},
            {"Sent": 5, "Failed": 1},
        )
        self.assertIn("Content items: 3", lines)
        self.assertIn("Content items (article): 2", lines)
        self.assertIn("Content items (video): 1", lines)
        self.assertIn("Dispatches: 6", lines)
        self.assertIn("Dispatches (Failed): 1", lines)
        self.assertIn("Dispatches (Sent): 5", lines)

    def test_experiments_are_capped_at_five(self):
        experiments = [
            {"status": "Running", "primary_metric": "conversions", "variants": []}
            for _ in range(CAMPAIGN_EXPERIMENTS_MAX + 2)
        ]
        lines = build_campaign_facts(self._campaign(), self._segment(), self._metrics(), experiments, {}, {})
        text = "\n".join(lines)

        self.assertIn(f"Experiments: {CAMPAIGN_EXPERIMENTS_MAX + 2}", lines)
        self.assertIn(f"Experiment {CAMPAIGN_EXPERIMENTS_MAX} status: Running", lines)
        self.assertNotIn(f"Experiment {CAMPAIGN_EXPERIMENTS_MAX + 1} status", text)
        self.assertIn("(+2 more experiments)", lines)

    def test_variants_are_capped_at_six(self):
        variants = [
            {"allocation": Decimal("10.00"), "is_control": False, "conversions": index, "cvr": None, "roas": None}
            for index in range(CAMPAIGN_VARIANTS_MAX + 2)
        ]
        experiments = [{"status": "Running", "primary_metric": "conversions", "variants": variants}]
        lines = build_campaign_facts(self._campaign(), self._segment(), self._metrics(), experiments, {}, {})
        text = "\n".join(lines)

        self.assertIn(f"Experiment 1 variants: {CAMPAIGN_VARIANTS_MAX + 2}", lines)
        self.assertIn(f"Experiment 1 variant {CAMPAIGN_VARIANTS_MAX} conversions: {CAMPAIGN_VARIANTS_MAX - 1}", lines)
        self.assertNotIn(f"Experiment 1 variant {CAMPAIGN_VARIANTS_MAX + 1} conversions", text)
        self.assertIn("Experiment 1 (+2 more variants)", lines)

    def test_content_and_dispatch_lines_are_capped_at_ten(self):
        content = {f"type{index:02d}": index for index in range(CAMPAIGN_COUNT_LINES_MAX + 3)}
        dispatch = {f"status{index:02d}": index for index in range(CAMPAIGN_COUNT_LINES_MAX + 2)}
        lines = build_campaign_facts(self._campaign(), self._segment(), self._metrics(), [], content, dispatch)

        self.assertIn(f"Content items: {sum(content.values())}", lines)
        self.assertIn("(+3 more content types)", lines)
        self.assertIn(f"Dispatches: {sum(dispatch.values())}", lines)
        self.assertIn("(+2 more dispatch statuses)", lines)
        self.assertEqual(
            sum(1 for line in lines if line.startswith("Content items (")),
            CAMPAIGN_COUNT_LINES_MAX,
        )
        self.assertEqual(
            sum(1 for line in lines if line.startswith("Dispatches (")),
            CAMPAIGN_COUNT_LINES_MAX,
        )

    def test_the_loader_returns_none_for_another_tenant(self):
        db = MagicMock()
        with patch.object(
            screen_facts.CampaignDraftRepository,
            "get_campaign",
            side_effect=CampaignDraftNotFoundError("not found"),
        ):
            self.assertIsNone(load_campaign_facts(db, TENANT, CAMPAIGN, None))

    def test_the_loader_returns_none_without_an_id(self):
        db = MagicMock()
        self.assertIsNone(load_campaign_facts(db, TENANT, None, None))
        db.execute.assert_not_called()

    def test_the_loader_builds_the_facts_from_the_tenant_checked_campaign(self):
        db = MagicMock()
        with patch.object(screen_facts.CampaignDraftRepository, "get_campaign", return_value=self._campaign()) as get, patch.object(
            screen_facts, "_campaign_segment", return_value=self._segment()
        ), patch.object(screen_facts, "_campaign_metrics", return_value=self._metrics()), patch.object(
            screen_facts, "_campaign_experiments", return_value=[]
        ), patch.object(screen_facts, "_campaign_content_counts", return_value={"article": 1}), patch.object(
            screen_facts, "_campaign_dispatch_counts", return_value={"Sent": 2}
        ):
            loaded = load_campaign_facts(db, TENANT, CAMPAIGN, None)

        get.assert_called_once_with(TENANT, CAMPAIGN)
        self.assertEqual(loaded.title, "Campaign on screen")
        self.assertEqual(loaded.label, "this campaign's details")
        self.assertIn("Content items: 1", loaded.lines)
        self.assertIn("Dispatches: 2", loaded.lines)


class AnalyticsFactsTests(unittest.TestCase):
    def _patch(self):
        """Patch the two repositories the analytics loader calls, with empty results."""
        event_repo = MagicMock()
        event_repo.return_value.query_daily_totals.return_value = []
        event_repo.return_value.query_device_type_totals.return_value = []
        reporting = MagicMock()
        reporting.return_value.count_master_profiles.return_value = 0
        reporting.return_value.raw_profiles_by_source_system.return_value = []
        return event_repo, reporting

    def test_the_default_period_is_thirty_days(self):
        db = MagicMock()
        event_repo, reporting = self._patch()
        with patch.object(screen_facts, "EventQueryRepository", event_repo), patch.object(
            screen_facts, "ReportingRepository", reporting
        ):
            loaded = load_analytics_facts(db, TENANT, None, None)

        self.assertEqual(loaded.title, f"Analytics for the last {ANALYTICS_DEFAULT_DAYS} days")
        self.assertEqual(loaded.label, "this dashboard's numbers")
        event_repo.return_value.query_daily_totals.assert_called_once_with(db, TENANT, days=ANALYTICS_DEFAULT_DAYS)
        reporting.return_value.count_master_profiles.assert_called_once_with(TENANT, days=ANALYTICS_DEFAULT_DAYS)

    def test_the_period_is_clamped_to_one_and_four_hundred(self):
        db = MagicMock()
        event_repo, reporting = self._patch()
        with patch.object(screen_facts, "EventQueryRepository", event_repo), patch.object(
            screen_facts, "ReportingRepository", reporting
        ):
            low = load_analytics_facts(db, TENANT, None, 0)
            high = load_analytics_facts(db, TENANT, None, 9999)

        self.assertEqual(low.title, "Analytics for the last 1 days")
        self.assertEqual(high.title, f"Analytics for the last {PERIOD_MAX_DAYS} days")

    def test_the_session_tenant_is_passed_and_no_master_profile_id(self):
        db = MagicMock()
        event_repo, reporting = self._patch()
        with patch.object(screen_facts, "EventQueryRepository", event_repo), patch.object(
            screen_facts, "ReportingRepository", reporting
        ):
            load_analytics_facts(db, TENANT, None, 30)

        for call in (
            event_repo.return_value.query_daily_totals.call_args,
            event_repo.return_value.query_device_type_totals.call_args,
            reporting.return_value.count_master_profiles.call_args,
            reporting.return_value.raw_profiles_by_source_system.call_args,
        ):
            self.assertNotIn("master_profile_id", call.kwargs)
        self.assertEqual(event_repo.return_value.query_device_type_totals.call_args.args, (db, TENANT))
        self.assertEqual(reporting.return_value.raw_profiles_by_source_system.call_args.args, (TENANT,))

    def test_a_failed_event_query_degrades_to_an_unavailable_line(self):
        db = MagicMock()
        event_repo = MagicMock()
        event_repo.return_value.query_daily_totals.side_effect = RuntimeError("event lake down")
        reporting = MagicMock()
        reporting.return_value.count_master_profiles.return_value = 3
        reporting.return_value.raw_profiles_by_source_system.return_value = []
        with patch.object(screen_facts, "EventQueryRepository", event_repo), patch.object(
            screen_facts, "ReportingRepository", reporting
        ):
            loaded = load_analytics_facts(db, TENANT, None, 30)

        self.assertIn("Event data: unavailable", loaded.lines)
        self.assertIn("Total master profiles: 3", loaded.lines)
        self.assertIn("Conversions is not available yet", loaded.lines)

    def test_daily_totals_are_summarised_and_capped(self):
        db = MagicMock()
        event_repo = MagicMock()
        event_repo.return_value.query_daily_totals.return_value = [
            {"day": date(2026, 1, 1) + timedelta(days=index), "total": index}
            for index in range(DAILY_TOTALS_MAX + 5)
        ]
        event_repo.return_value.query_device_type_totals.return_value = [{"device_type": "mobile", "total": 7}]
        reporting = MagicMock()
        reporting.return_value.count_master_profiles.return_value = 0
        reporting.return_value.raw_profiles_by_source_system.return_value = []
        with patch.object(screen_facts, "EventQueryRepository", event_repo), patch.object(
            screen_facts, "ReportingRepository", reporting
        ):
            loaded = load_analytics_facts(db, TENANT, None, 30)

        self.assertIn("Total events: 171", loaded.lines)
        self.assertIn("Active days: 18", loaded.lines)
        self.assertIn("Peak day: 2026-01-19 (18 events)", loaded.lines)
        self.assertIn("Events by device (mobile): 7", loaded.lines)
        self.assertIn("(+5 more days)", loaded.lines)
        self.assertEqual(sum(1 for line in loaded.lines if line.startswith("Daily ")), DAILY_TOTALS_MAX)
        # The series is oldest-first: the cap keeps the latest days, not the earliest.
        self.assertIn("Daily 2026-01-19: 18", loaded.lines)
        self.assertNotIn("Daily 2026-01-01: 0", loaded.lines)

    def test_source_system_rows_are_capped_at_ten(self):
        db = MagicMock()
        event_repo, _ = self._patch()
        reporting = MagicMock()
        reporting.return_value.count_master_profiles.return_value = 0
        reporting.return_value.raw_profiles_by_source_system.return_value = [
            {"source_system": f"src{index}", "domain": "retail", "count": index}
            for index in range(SOURCE_SYSTEM_TOP + 4)
        ]
        with patch.object(screen_facts, "EventQueryRepository", event_repo), patch.object(
            screen_facts, "ReportingRepository", reporting
        ):
            loaded = load_analytics_facts(db, TENANT, None, 30)

        self.assertEqual(sum(1 for line in loaded.lines if line.startswith("Profiles (")), SOURCE_SYSTEM_TOP)


class OverviewFactsTests(unittest.TestCase):
    def _summary(self):
        return SimpleNamespace(
            total_raw_profiles=100,
            total_master_profiles=40,
            duplicate_master_profile_count=5,
            processed_raw_profiles=80,
            in_progress_raw_profiles=10,
            pending_raw_profiles=10,
            raw_profiles_by_status=[SimpleNamespace(label="processed", count=80)],
            raw_profiles_by_domain=[SimpleNamespace(domain="retail", count=100)],
            master_profiles_by_domain=[SimpleNamespace(domain="retail", count=40)],
            raw_profiles_by_source_system=[SimpleNamespace(source_system="web", domain="retail", count=100)],
        )

    def _coverage(self):
        return SimpleNamespace(
            total_master_profiles=40,
            with_email=20,
            with_phone_number=10,
            with_device_id=0,
            with_advertising_id=0,
            with_cookie_id=0,
            with_external_id=0,
            with_national_id=0,
        )

    def test_coverage_is_read_from_the_dict_the_dao_really_returns(self):
        coverage = vars(self._coverage())  # the DAO returns a plain dict, not an attribute object
        lines = screen_facts._coverage_lines(coverage)

        self.assertIn("Identity coverage (Email): 50.0%", lines)
        self.assertIn("Identity coverage (Phone): 25.0%", lines)
        self.assertEqual(len(lines), 7)

    def test_the_default_period_is_ninety_days(self):
        db = MagicMock()
        reporting = MagicMock()
        reporting.return_value.get_cir_summary.return_value = self._summary()
        reporting.return_value.get_identity_graph_coverage.return_value = self._coverage()
        with patch.object(screen_facts, "ReportingRepository", reporting):
            loaded = load_overview_facts(db, TENANT, None, None)

        self.assertEqual(loaded.title, f"Overview for the last {OVERVIEW_DEFAULT_DAYS} days")
        self.assertEqual(loaded.label, "this dashboard's numbers")
        reporting.return_value.get_cir_summary.assert_called_once_with(TENANT, days=OVERVIEW_DEFAULT_DAYS)
        reporting.return_value.get_identity_graph_coverage.assert_called_once_with(TENANT, days=OVERVIEW_DEFAULT_DAYS)
        for call in (
            reporting.return_value.get_cir_summary.call_args,
            reporting.return_value.get_identity_graph_coverage.call_args,
        ):
            self.assertNotIn("master_profile_id", call.kwargs)

    def test_the_period_is_clamped(self):
        db = MagicMock()
        reporting = MagicMock()
        reporting.return_value.get_cir_summary.return_value = self._summary()
        reporting.return_value.get_identity_graph_coverage.return_value = self._coverage()
        with patch.object(screen_facts, "ReportingRepository", reporting):
            low = load_overview_facts(db, TENANT, None, -5)
            high = load_overview_facts(db, TENANT, None, 500)

        self.assertEqual(low.title, "Overview for the last 1 days")
        self.assertEqual(high.title, f"Overview for the last {PERIOD_MAX_DAYS} days")

    def test_the_counts_breakdowns_and_coverage_are_sent(self):
        db = MagicMock()
        reporting = MagicMock()
        reporting.return_value.get_cir_summary.return_value = self._summary()
        reporting.return_value.get_identity_graph_coverage.return_value = self._coverage()
        with patch.object(screen_facts, "ReportingRepository", reporting):
            loaded = load_overview_facts(db, TENANT, None, 90)

        self.assertIn("Raw profiles: 100", loaded.lines)
        self.assertIn("Master profiles: 40", loaded.lines)
        self.assertIn("Duplicate master profiles: 5", loaded.lines)
        self.assertIn("Processed raw profiles: 80", loaded.lines)
        self.assertIn("In-progress raw profiles: 10", loaded.lines)
        self.assertIn("Pending raw profiles: 10", loaded.lines)
        self.assertIn("Raw profiles by status (processed): 80", loaded.lines)
        self.assertIn("Raw profiles by domain (retail): 100", loaded.lines)
        self.assertIn("Master profiles by domain (retail): 40", loaded.lines)
        self.assertIn("Raw profiles (web / retail): 100", loaded.lines)
        self.assertIn("Identity coverage (Email): 50.0%", loaded.lines)
        self.assertIn("Identity coverage (Phone): 25.0%", loaded.lines)
        self.assertIn("Identity coverage (Device ID): 0.0%", loaded.lines)

    def test_a_failed_query_degrades_to_an_unavailable_line(self):
        db = MagicMock()
        reporting = MagicMock()
        reporting.return_value.get_cir_summary.side_effect = RuntimeError("db down")
        reporting.return_value.get_identity_graph_coverage.side_effect = RuntimeError("db down")
        with patch.object(screen_facts, "ReportingRepository", reporting):
            loaded = load_overview_facts(db, TENANT, None, 90)

        self.assertIn("Profile data: unavailable", loaded.lines)
        self.assertIn("Identity coverage: unavailable", loaded.lines)


class PersonaFactsTests(unittest.TestCase):
    def _archetype(self, **extra):
        values = {
            "persona_archetype_id": PERSONA,
            "tenant_id": TENANT,
            "persona_name": "Loyal shoppers",
            "persona_summary": "Buy often and refer friends",
            "domain": "retail",
            "persona_code": "loyal",
            "persona_category": "value",
            "is_active": True,
            "matched_profile_count": 12,
            "centroid_behavior_score": Decimal("1.00"),
            "centroid_engagement_score": Decimal("2.00"),
            "centroid_financial_score": Decimal("3.00"),
            "centroid_loyalty_score": Decimal("4.00"),
            "centroid_relationship_score": Decimal("5.00"),
            "centroid_risk_score": Decimal("6.00"),
            "llm_provider": "openai",
            "llm_model": "gpt-4",
            "created_at": datetime(2026, 9, 1, 8, 0),
            "updated_at": datetime(2026, 9, 2, 8, 0),
            # everything below must never reach the prompt
            "persona_embedding": [0.111, 0.222],
            "matched_profiles": ["ZZMATCHED"],
        }
        values.update(extra)
        return SimpleNamespace(**values)

    def test_only_allowlisted_fields_are_sent(self):
        lines = build_persona_facts(self._archetype())
        text = "\n".join(lines)

        self.assertIn('Name (written by staff): "Loyal shoppers"', lines)
        self.assertIn('Summary (written by staff): "Buy often and refer friends"', lines)
        self.assertIn("Domain: retail", lines)
        self.assertIn("Code: loyal", lines)
        self.assertIn("Category: value", lines)
        self.assertIn("Active: yes", lines)
        self.assertIn("Matched profiles: 12", lines)
        self.assertIn("Centroid behavior score: 1.00", lines)
        self.assertIn("Centroid engagement score: 2.00", lines)
        self.assertIn("Centroid financial score: 3.00", lines)
        self.assertIn("Centroid loyalty score: 4.00", lines)
        self.assertIn("Centroid relationship score: 5.00", lines)
        self.assertIn("Centroid risk score: 6.00", lines)
        self.assertIn("LLM provider: openai", lines)
        self.assertIn("LLM model: gpt-4", lines)
        self.assertIn("Created: 2026-09-01", lines)
        self.assertIn("Updated: 2026-09-02", lines)
        for sentinel in ("ZZMATCHED", "0.111", "0.222"):
            self.assertNotIn(sentinel, text)

    def test_staff_text_is_masked_and_capped(self):
        lines = build_persona_facts(
            self._archetype(persona_name="Linh's persona linh@example.com", persona_summary="x" * 500)
        )
        self.assertIn('Name (written by staff): "Linh\'s persona [email]"', lines)
        summary = next(line for line in lines if line.startswith("Summary"))
        self.assertEqual(len(summary.split('"')[1]), STAFF_TEXT_MAX_CHARS)

    def test_the_loader_returns_none_for_another_tenant(self):
        db = MagicMock()
        db.get.return_value = self._archetype(tenant_id=uuid.uuid4())
        self.assertIsNone(load_persona_facts(db, TENANT, PERSONA, None))

    def test_the_loader_returns_none_without_an_id(self):
        db = MagicMock()
        self.assertIsNone(load_persona_facts(db, TENANT, None, None))
        db.get.assert_not_called()

    def test_the_loader_never_reads_the_matched_profiles(self):
        db = MagicMock()
        db.get.return_value = self._archetype()
        loaded = load_persona_facts(db, TENANT, PERSONA, None)

        self.assertEqual(loaded.title, "Persona on screen")
        self.assertEqual(loaded.label, "this persona's details")
        self.assertIn("Matched profiles: 12", loaded.lines)
        db.execute.assert_not_called()


class RegistryTests(unittest.TestCase):
    def test_the_segment_and_campaign_pages_are_registered(self):
        self.assertIs(SCREEN_FACTS[SEGMENT_PAGE], load_segment_facts)
        self.assertIs(SCREEN_FACTS[CAMPAIGN_PAGE], load_campaign_facts)
        self.assertIs(SCREEN_FACTS[CAMPAIGN_EDIT_PAGE], load_campaign_facts)

    def test_the_dashboard_and_persona_pages_are_registered(self):
        self.assertIs(SCREEN_FACTS[ANALYTICS_PAGE], load_analytics_facts)
        self.assertIs(SCREEN_FACTS[OVERVIEW_PAGE], load_overview_facts)
        self.assertIs(SCREEN_FACTS[PERSONA_PAGE], load_persona_facts)


if __name__ == "__main__":
    unittest.main()


class _PostgresLikeSession:
    """Models the one Postgres behaviour that matters here: after a failed statement the transaction
    is aborted and every later statement fails, unless the failure happened inside a savepoint."""

    def __init__(self):
        self.aborted = False
        self.savepoints = 0

    def begin_nested(self):
        session = self

        class _Savepoint:
            def __enter__(self_inner):
                session.savepoints += 1
                self_inner.was_aborted = session.aborted
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                if exc_type is not None:
                    session.aborted = self_inner.was_aborted  # rolling back the savepoint heals it
                return False

        return _Savepoint()

    def run(self, ok=True):
        if self.aborted:
            raise InternalError("stmt", {}, Exception("InFailedSqlTransaction: current transaction is aborted"))
        if not ok:
            self.aborted = True
            raise ProgrammingError("stmt", {}, Exception("relation does not exist"))
        return "rows"


class OptionalReadsDoNotPoisonTheRequestTests(unittest.TestCase):
    def test_one_failing_overview_aggregate_leaves_the_other_and_the_session_usable(self):
        db = _PostgresLikeSession()
        reporting = MagicMock()
        reporting.return_value.get_cir_summary.side_effect = lambda *a, **k: db.run(ok=False)
        reporting.return_value.get_identity_graph_coverage.side_effect = lambda *a, **k: (
            db.run() and {"total_master_profiles": 4, "with_email": 2, "with_phone_number": 0, "with_device_id": 0,
                          "with_advertising_id": 0, "with_cookie_id": 0, "with_external_id": 0, "with_national_id": 0}
        )
        with patch.object(screen_facts, "ReportingRepository", reporting):
            loaded = load_overview_facts(db, TENANT, None, 30)

        self.assertIn("Profile data: unavailable", loaded.lines)
        self.assertIn("Identity coverage (Email): 50.0%", loaded.lines)  # not collateral damage
        self.assertEqual(db.run(), "rows")  # the audit row can still be written afterwards

    def test_a_failing_segment_workflow_read_does_not_abort_the_session(self):
        db = MagicMock()
        state = _PostgresLikeSession()
        db.begin_nested.side_effect = state.begin_nested
        db.execute.side_effect = lambda *a, **k: state.run(ok=False)

        self.assertEqual(screen_facts._segment_workflow_steps(db, TENANT, uuid.uuid4()), [])
        self.assertEqual(state.run(), "rows")


class RuleValuesAreMaskedTests(unittest.TestCase):
    def test_an_email_or_phone_in_a_non_pii_rule_value_is_masked(self):
        catalog = {"lifecycle_stage": ("Lifecycle stage", False)}
        rules = {"condition": "AND", "rules": [
            {"field": "lifecycle_stage", "operator": "equal", "value": "lead john.doe@example.com 0901234567"}]}

        summary = screen_facts.summarize_segment_rules(rules, catalog)

        self.assertEqual(summary, "Lifecycle stage is lead [email] [phone]")
        self.assertNotIn("john.doe", summary)
        self.assertNotIn("0901234567", summary)


class CampaignExperimentQueryCostTests(unittest.TestCase):
    def test_only_the_experiments_that_are_described_cost_queries(self):
        experiments = [
            SimpleNamespace(experiment_id=uuid.uuid4(), status="running", primary_metric="cvr") for _ in range(8)
        ]
        db = MagicMock()

        def execute(statement, *args, **kwargs):
            result = MagicMock()
            sql = str(statement)
            result.scalars.return_value.all.return_value = (
                experiments if "campaign_experiments" in sql and "variants" not in sql else []
            )
            return result

        db.execute.side_effect = execute
        performance_calls = []

        class FakeRepo:
            def __init__(self, session):
                pass

            def performance(self, tenant_id, experiment_id):
                performance_calls.append(experiment_id)
                return []

        with patch.object(screen_facts, "CampaignExperimentRepository", FakeRepo):
            result = screen_facts._campaign_experiments(db, TENANT, uuid.uuid4())

        self.assertEqual(len(result), 8)  # all are counted
        self.assertEqual(len(performance_calls), screen_facts.CAMPAIGN_EXPERIMENTS_MAX)
        self.assertEqual(db.execute.call_count, 1 + screen_facts.CAMPAIGN_EXPERIMENTS_MAX)
