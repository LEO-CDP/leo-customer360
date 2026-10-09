"""Unit tests for ``POST /assistant/ask``: page context, audit log, and PII masking."""

import unittest
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories import screen_facts
from core.repositories.assistant_repository import AssistantRepository, ConversationRepository, build_profile_facts
from core.repositories.screen_facts import ScreenFacts
from core.routers import assistant_api
from leo_customer360_dao.models.system import SysAuditLog

TENANT = uuid.uuid4()
USER = uuid.uuid4()
PROFILE = uuid.uuid4()
DOCS_ANSWER = {
    "answer": "This page shows one customer.",
    "found": True,
    "status": "answered",
    "missing": [],
    "clarify": None,
    "sources": [{"path": "data-sources/x.md", "title": "X", "heading": "X"}],
}


class MaskQuestionTests(unittest.TestCase):
    def test_emails_and_phone_numbers_are_masked(self):
        text = "Why did linh.nguyen@example.com (0901 234 567, +84 901 234 567) churn?"
        masked = assistant_api.mask_text(text, 500)
        self.assertNotIn("@", masked)
        self.assertNotRegex(masked, r"\d{4}")
        self.assertEqual(masked, "Why did [email] ([phone], [phone]) churn?")

    def test_ordinary_numbers_and_text_survive_and_length_is_capped(self):
        self.assertEqual(assistant_api.mask_text("Top 5 customers in 2026", 500), "Top 5 customers in 2026")
        self.assertEqual(len(assistant_api.mask_text("x" * 5000, 500)), 500)
        self.assertEqual(assistant_api.mask_text("", 500), "")

    def test_dates_and_figures_are_not_taken_for_phone_numbers(self):
        for text in ("report for 2026-10-06 please", "import 1 000 000 rows", "version 1.2.3.4", "call at 12:30"):
            self.assertEqual(assistant_api.mask_text(text, 500), text)
        self.assertEqual(assistant_api.mask_text("id 123456789012 and 028 3822 1234", 500), "id [phone] and [phone]")


class SanitizeLabelTests(unittest.TestCase):
    def test_whitespace_and_control_characters_collapse_to_single_spaces(self):
        self.assertEqual(assistant_api.sanitize_label("  Agent   Workflow\nOrdered\tprocessing ", 60),
                         "Agent Workflow Ordered processing")
        self.assertEqual(assistant_api.sanitize_label("Timeline\n\n  Tab\x00", 60), "Timeline Tab")

    def test_pii_is_masked_and_unicode_is_kept(self):
        self.assertEqual(assistant_api.sanitize_label("Edit user jane@x.com", 80), "Edit user [email]")
        self.assertEqual(assistant_api.sanitize_label("Tổng quan", 60), "Tổng quan")

    def test_blank_or_missing_becomes_none(self):
        for text in (None, "", "   ", "\n\t\x00"):
            self.assertIsNone(assistant_api.sanitize_label(text, 60), text)

    def test_the_result_is_capped(self):
        self.assertEqual(assistant_api.sanitize_label("x" * 200, 60), "x" * 60)


class _AskHarness(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()

        @self.app.middleware("http")
        async def _identity(request, call_next):
            request.state.tenant_id = str(TENANT)
            request.state.user_id = str(USER)
            return await call_next(request)

        self.app.include_router(assistant_api.assistant_router)
        self.db = MagicMock()
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(self.app)

    def _ask(self, docs=None, facts=(), **body):
        with patch("core.routers.assistant_api.ask_docs", return_value=docs or DOCS_ANSWER) as ask, patch.object(
            screen_facts.AssistantRepository, "profile_facts", return_value=None if facts is None else list(facts)
        ) as load:
            response = self.client.post("/assistant/ask", json={"question": "What is this page?", **body})
        self.loaded = load
        return response, ask


class AssistantAskTests(_AskHarness):
    def test_the_page_is_forwarded_and_the_structured_reply_comes_back(self):
        response, ask = self._ask(page="/profiles/:id", master_profile_id=str(PROFILE))

        self.loaded.assert_called_once_with(TENANT, PROFILE)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["found"], data["status"]), (True, "answered"))
        self.assertEqual(data["sources"][0]["path"], "data-sources/x.md")
        ask.assert_called_once_with(
            "What is this page?", page="/profiles/:id", context=None,
            context_title="Customer profile on screen", view=None, dialog=None, history=None, summary=None,
        )

    def test_the_basis_line_is_passed_through_and_absent_when_unknown(self):
        response, _ = self._ask(docs={**DOCS_ANSWER, "basis": {"page_guide": True, "profile_data": False}})
        self.assertEqual(
            response.json()["basis"],
            {"page_guide": True, "profile_data": False, "screen_data": False, "screen_label": None},
        )

        response, _ = self._ask()
        self.assertIsNone(response.json()["basis"])

    def test_every_ask_is_recorded_with_the_masked_question(self):
        response, _ = self._ask(
            page="/profiles/:id",
            master_profile_id=str(PROFILE),
            view="Timeline",
            question="Why did linh@example.com churn? call 0901234567",
        )

        self.assertEqual(response.status_code, 200)
        row = self.db.add.call_args.args[0]
        self.assertIsInstance(row, SysAuditLog)
        self.assertEqual((row.tenant_id, row.user_id), (TENANT, USER))
        self.assertEqual((row.action, row.resource_type, row.resource_id), ("ASK", "leo_assistant", str(PROFILE)))
        self.assertEqual(row.after_data["question"], "Why did [email] churn? call [phone]")
        self.assertEqual(row.after_data["page"], "/profiles/:id")
        self.assertEqual(row.after_data["view"], "Timeline")
        self.assertEqual(row.after_data["status"], "answered")
        self.assertEqual(row.after_data["source_paths"], ["data-sources/x.md"])
        self.assertIsInstance(row.after_data["latency_ms"], int)
        self.assertNotIn("linh@example.com", str(row.after_data))
        self.db.commit.assert_called_once()

    def test_without_a_profile_the_log_points_at_the_page(self):
        self._ask(page="/segments")
        self.assertEqual(self.db.add.call_args.args[0].resource_id, "/segments")

    def test_a_logging_failure_never_costs_the_user_the_answer(self):
        self.db.commit.side_effect = RuntimeError("db down")

        response, _ = self._ask(page="/segments")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], DOCS_ANSWER["answer"])
        self.db.rollback.assert_called_once()

    def test_docs_service_failure_is_a_502_and_nothing_is_logged(self):
        with patch("core.routers.support_api.httpx.Client.post", side_effect=httpx.ConnectError("down")):
            response = self.client.post("/assistant/ask", json={"question": "Q?", "page": "/segments"})
        self.assertEqual(response.status_code, 502)
        self.db.add.assert_not_called()

    def test_tenant_and_user_come_from_the_session_not_the_body(self):
        other = str(uuid.uuid4())
        self._ask(page="/segments", tenant_id=other, user_id=other)
        row = self.db.add.call_args.args[0]
        self.assertEqual((row.tenant_id, row.user_id), (TENANT, USER))

    def test_bad_input_is_rejected(self):
        for body in (
            {"question": ""},
            {"question": "x" * 2001},
            {"question": "Q?", "page": "https://evil.example/profiles"},
            {"question": "Q?", "page": "/a b"},
            {"question": "Q?", "page": "/" + "a" * 130},
            {"question": "Q?", "view": "x" * 61},
            {"question": "Q?", "dialog": "x" * 81},
            {"question": "Q?", "master_profile_id": "not-a-uuid"},
            {"question": "Q?", "entity_id": "not-a-uuid"},
            {"question": "Q?", "page": "/overview", "period_days": 0},
            {"question": "Q?", "page": "/overview", "period_days": 401},
            {"question": "Q?", "page": "/overview", "period_days": -1},
        ):
            with patch("core.routers.assistant_api.ask_docs") as ask:
                response = self.client.post("/assistant/ask", json=body)
            self.assertEqual(response.status_code, 422, body)
            ask.assert_not_called()

    def test_page_is_optional(self):
        response, ask = self._ask()
        self.assertEqual(response.status_code, 200)
        ask.assert_called_once_with(
            "What is this page?", page=None, context=None, context_title=None, view=None, dialog=None, history=None, summary=None,
        )

    def test_view_and_dialog_reach_the_docs_service_sanitized(self):
        response, ask = self._ask(
            page="/segments/:id",
            view="  Agent   Workflow\nOrdered\tprocessing steps ",
            dialog="Edit user jane@x.com",
        )

        self.assertEqual(response.status_code, 200)
        ask.assert_called_once_with(
            "What is this page?",
            page="/segments/:id",
            context=None,
            context_title=None,
            view="Agent Workflow Ordered processing steps",
            dialog="Edit user [email]",
            history=None, summary=None,
        )

    def test_control_characters_and_newlines_in_labels_are_collapsed(self):
        _, ask = self._ask(page="/segments", view="Timeline\n\n  Tab\x00", dialog="Add\tData  Source")

        ask.assert_called_once_with(
            "What is this page?",
            page="/segments",
            context=None,
            context_title=None,
            view="Timeline Tab",
            dialog="Add Data Source",
            history=None, summary=None,
        )

    def test_an_email_in_a_dialog_title_is_masked_in_the_audit_row(self):
        response, _ = self._ask(page="/segments", dialog="Edit user jane@x.com")

        self.assertEqual(response.status_code, 200)
        after = self.db.add.call_args.args[0].after_data
        self.assertEqual(after["dialog"], "Edit user [email]")
        self.assertNotIn("jane@x.com", str(after))

    def test_missing_tenant_is_a_400(self):
        bare = FastAPI()
        bare.include_router(assistant_api.assistant_router)
        bare.dependency_overrides[get_db] = lambda: MagicMock()
        self.assertEqual(TestClient(bare).post("/assistant/ask", json={"question": "Q?"}).status_code, 400)


    def test_profile_facts_are_loaded_in_the_callers_tenant_and_sent_with_the_question(self):
        facts = ["Churn risk tier (churn_risk_tier): high — Bucketized churn risk", "Next best action: call back"]
        response, ask = self._ask(facts=facts, page="/profiles/:id", master_profile_id=str(PROFILE))

        self.assertEqual(response.status_code, 200)
        self.loaded.assert_called_once_with(TENANT, PROFILE)
        ask.assert_called_once_with(
            "What is this page?", page="/profiles/:id", context=facts,
            context_title="Customer profile on screen", view=None, dialog=None, history=None, summary=None,
        )
        # The log records WHICH fields were sent, never their values, and which loader ran.
        after = self.db.add.call_args.args[0].after_data
        self.assertEqual(after["fact_fields"], ["Churn risk tier", "Next best action"])
        self.assertEqual(after["facts_page"], "/profiles/:id")
        self.assertNotIn("profile_fields", after)
        self.assertNotIn("high", str(after["fact_fields"]))

    def test_a_profile_outside_the_tenant_is_a_404_and_the_docs_service_is_never_called(self):
        response, ask = self._ask(facts=None, page="/profiles/:id", master_profile_id=str(PROFILE))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Profile not found.")
        ask.assert_not_called()
        self.db.add.assert_not_called()

    def test_no_profile_id_means_no_lookup(self):
        _, ask = self._ask(page="/segments")
        self.loaded.assert_not_called()
        ask.assert_called_once_with(
            "What is this page?", page="/segments", context=None, context_title=None, view=None, dialog=None,
            history=None, summary=None,
        )

    def test_entity_id_and_master_profile_id_are_equivalent_on_the_profile_page(self):
        facts = ["Next best action: call back"]
        for body in (
            {"master_profile_id": str(PROFILE)},
            {"entity_id": str(PROFILE)},
            {"entity_id": str(PROFILE), "master_profile_id": str(PROFILE)},
        ):
            response, ask = self._ask(facts=facts, page="/profiles/:id", **body)
            self.assertEqual(response.status_code, 200, body)
            self.loaded.assert_called_once_with(TENANT, PROFILE)
            ask.assert_called_once_with(
                "What is this page?", page="/profiles/:id", context=facts,
                context_title="Customer profile on screen", view=None, dialog=None, history=None, summary=None,
            )

    def test_conflicting_ids_on_the_profile_page_are_a_422(self):
        response, ask = self._ask(
            page="/profiles/:id", entity_id=str(PROFILE), master_profile_id=str(uuid.uuid4())
        )
        self.assertEqual(response.status_code, 422)
        self.loaded.assert_not_called()
        ask.assert_not_called()

    def test_an_id_page_without_an_id_answers_without_facts(self):
        response, ask = self._ask(page="/profiles/:id")
        self.assertEqual(response.status_code, 200)
        self.loaded.assert_not_called()
        ask.assert_called_once_with(
            "What is this page?", page="/profiles/:id", context=None, context_title=None, view=None, dialog=None,
            history=None, summary=None,
        )

    def test_a_page_without_a_loader_answers_without_facts(self):
        response, ask = self._ask(page="/segments", period_days=30)
        self.assertEqual(response.status_code, 200)
        self.loaded.assert_not_called()
        ask.assert_called_once_with(
            "What is this page?", page="/segments", context=None, context_title=None, view=None, dialog=None,
            history=None, summary=None,
        )
        after = self.db.add.call_args.args[0].after_data
        self.assertEqual(after["fact_fields"], [])
        self.assertIsNone(after["facts_page"])

    def test_a_loader_added_for_another_id_page_receives_the_entity_id_and_period(self):
        seen = {}

        def loader(db, tenant_id, entity_id, period_days):
            seen.update(tenant_id=tenant_id, entity_id=entity_id, period_days=period_days)
            return ScreenFacts(
                title="Segment on screen",
                label="this segment's details",
                lines=["Name: X"],
                fields=["Name"],
            )

        with patch.dict(screen_facts.SCREEN_FACTS, {"/segments/:id": loader}):
            response, ask = self._ask(page="/segments/:id", entity_id=str(PROFILE), period_days=30)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, {"tenant_id": TENANT, "entity_id": PROFILE, "period_days": 30})
        ask.assert_called_once_with(
            "What is this page?", page="/segments/:id", context=["Name: X"],
            context_title="Segment on screen", view=None, dialog=None, history=None, summary=None,
        )
        self.assertEqual(self.db.add.call_args.args[0].after_data["facts_page"], "/segments/:id")

    def test_a_missing_object_on_another_id_page_is_a_generic_404(self):
        with patch.dict(screen_facts.SCREEN_FACTS, {"/segments/:id": lambda *args: None}):
            response, ask = self._ask(page="/segments/:id", entity_id=str(PROFILE))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Not found.")
        ask.assert_not_called()

    def test_period_days_at_the_bounds_is_accepted(self):
        for days in (1, 400):
            response, _ = self._ask(page="/overview", period_days=days)
            self.assertEqual(response.status_code, 200, days)

    def test_screen_label_is_present_only_when_facts_were_sent(self):
        docs = {**DOCS_ANSWER, "basis": {"page_guide": True, "profile_data": True}}
        facts = ["Next best action: call back"]
        response, _ = self._ask(docs=docs, facts=facts, page="/profiles/:id", master_profile_id=str(PROFILE))
        self.assertEqual(
            response.json()["basis"],
            {"page_guide": True, "profile_data": True, "screen_data": True,
             "screen_label": "this customer's profile"},
        )

        response, _ = self._ask(docs=docs, page="/segments")
        self.assertIsNone(response.json()["basis"]["screen_label"])
        # screen_data is copied from profile_data when the docs service did not send it yet
        self.assertTrue(response.json()["basis"]["screen_data"])

    def test_screen_data_from_the_docs_service_is_passed_through(self):
        docs = {**DOCS_ANSWER, "basis": {"page_guide": False, "profile_data": False, "screen_data": True}}
        response, _ = self._ask(docs=docs, page="/overview")
        self.assertTrue(response.json()["basis"]["screen_data"])


class ProfileFactsTests(unittest.TestCase):
    CATALOG = {
        "lifecycle_stage": ("Current stage in the journey.", False),
        "churn_risk_tier": ("Bucketized churn risk (low/medium/high/critical).", False),
        "churn_probability": ("ML-predicted probability.", False),
        "segmentation_tags": ("Computed labels.", False),
        "last_activity_at": ("Most recent activity.", False),
        "persona_name": ("Non-PII label.", False),
    }

    def _profile(self, **extra):
        values = {
            "lifecycle_stage": "customer", "churn_risk_tier": "high", "churn_probability": Decimal("0.3780"),
            "segmentation_tags": ["gen_z", "frequent"], "last_activity_at": datetime(2026, 10, 1, 8, 30), "persona_name": None,
            # everything below must never reach the prompt
            "full_name": "ZZPII Linh Nguyen", "email": "ZZPII@example.com", "phone_number": "ZZPII0901234567",
            "address": "ZZPII 1 Le Loi", "attributes": {"note": "ZZPII"}, "communication_preferences": {"x": "ZZPII"},
            "persona_summary": "ZZPII Linh is a loyal shopper", "external_ids": ["ZZPII"], "date_of_birth": "ZZPII",
        }
        values.update(extra)
        return SimpleNamespace(**values)

    def test_only_allowlisted_non_pii_fields_are_sent_and_catalog_help_is_attached(self):
        lines = build_profile_facts(self._profile(), None, self.CATALOG)

        self.assertIn("Lifecycle stage (lifecycle_stage): customer — Current stage in the journey.", lines)
        self.assertIn("Churn probability (churn_probability): 0.38 — ML-predicted probability.", lines)
        self.assertIn("Segmentation tags (segmentation_tags): gen_z, frequent — Computed labels.", lines)
        self.assertIn("Last activity (last_activity_at): 2026-10-01 — Most recent activity.", lines)
        self.assertEqual(len(lines), 6)  # persona_name is None: nothing to say; plus the "no persona yet" line
        self.assertIn("Persona: not computed yet for this customer, so there is no Next Best Action", lines)

    def test_no_pii_sentinel_ever_appears_in_the_facts(self):
        persona = SimpleNamespace(next_best_action="Call back", risk_level="medium", full_name="ZZPII", persona_summary="ZZPII")
        text = "\n".join(build_profile_facts(self._profile(), persona, self.CATALOG))
        self.assertNotIn("ZZPII", text)
        self.assertIn("Next best action: Call back", text)

    def test_a_field_marked_pii_or_missing_from_the_catalog_is_not_sent(self):
        catalog = dict(self.CATALOG, churn_risk_tier=("Churn risk.", True))
        del catalog["lifecycle_stage"]

        text = "\n".join(build_profile_facts(self._profile(), None, catalog))

        self.assertNotIn("churn_risk_tier", text)
        self.assertNotIn("lifecycle_stage", text)
        self.assertIn("churn_probability", text)

    def test_persona_fields_are_formatted_and_empty_ones_skipped(self):
        persona = SimpleNamespace(
            customer_value_tier="high", risk_level=None, persona_score=Decimal("71.5"), next_best_action="",
            match_score=None, confidence_score=Decimal("0.8123"),
        )
        lines = build_profile_facts(SimpleNamespace(), persona, {})
        self.assertEqual(lines, ["Customer value tier: high", "Persona score: 71.50", "Persona confidence: 0.81"])

    def test_the_repository_refuses_a_profile_from_another_tenant(self):
        session = MagicMock()
        session.get.return_value = SimpleNamespace(tenant_id=uuid.uuid4(), current_persona_id=None)
        self.assertIsNone(AssistantRepository(session).profile_facts(TENANT, PROFILE))
        session.execute.assert_not_called()

        session.get.return_value = None
        self.assertIsNone(AssistantRepository(session).profile_facts(TENANT, PROFILE))


HISTORY = [
    {"role": "user", "text": "how can i use profile scope ?", "clarify": False},
    {"role": "assistant", "text": "Which feature do you mean?", "clarify": True},
]
CONVERSATION = uuid.uuid4()


class ConversationMemoryTests(_AskHarness):
    def _ask_in_chat(self, docs=None, window=(CONVERSATION, HISTORY, None), **body):
        with patch.object(ConversationRepository, "window", return_value=window) as win, patch.object(
            ConversationRepository, "add_exchange"
        ) as add:
            response, ask = self._ask(docs=docs, page="/segments", **body)
        return response, ask, win, add

    def test_the_saved_window_is_sent_with_the_question_and_the_conversation_id_comes_back(self):
        response, ask, win, add = self._ask_in_chat(question="it is data sources", conversation_id=str(CONVERSATION))

        win.assert_called_once_with(TENANT, USER, CONVERSATION, "/segments", None)
        self.assertEqual(ask.call_args.kwargs["history"], HISTORY)
        self.assertEqual(response.json()["conversation_id"], str(CONVERSATION))

    def test_the_question_is_masked_before_the_model_and_before_storage(self):
        response, ask, _, add = self._ask_in_chat(question="why did linh@example.com (0901234567) churn?")

        masked = "why did [email] ([phone]) churn?"
        self.assertEqual(ask.call_args.args[0], masked)
        self.assertEqual(add.call_args.args[5], masked)
        self.assertNotIn("linh@example.com", str(ask.call_args) + str(add.call_args))

    def test_the_exchange_is_stored_with_only_the_documents_the_answer_used(self):
        _, _, _, add = self._ask_in_chat(question="Q?")

        args = add.call_args.args
        self.assertEqual(args[:5], (TENANT, USER, CONVERSATION, "/segments", None))
        self.assertEqual(args[6:9], (DOCS_ANSWER["answer"], "answered", None))
        self.assertEqual(args[9], [{"path": "data-sources/x.md", "title": "X", "heading": "X"}])

    def test_the_running_summary_goes_to_the_model_and_the_new_one_is_masked_and_stored(self):
        docs = {**DOCS_ANSWER, "summary": "Asked about linh@example.com; Growth allows 25 users."}
        _, ask, _, add = self._ask_in_chat(
            docs=docs, window=(CONVERSATION, HISTORY, "Growth plan question."), question="Q?"
        )
        self.assertEqual(ask.call_args.kwargs["summary"], "Growth plan question.")
        self.assertEqual(add.call_args.args[10], "Asked about [email]; Growth allows 25 users.")

    def test_no_summary_from_the_model_stores_none(self):
        _, _, _, add = self._ask_in_chat(question="Q?")
        self.assertIsNone(add.call_args.args[10])

    def test_a_refusal_is_stored_without_sources(self):
        refusal = {**DOCS_ANSWER, "found": False, "status": "not_found", "clarify": "question"}
        _, _, _, add = self._ask_in_chat(docs=refusal, question="Q?")
        self.assertEqual(add.call_args.args[8:10], ("question", []))

    def test_a_failed_save_never_costs_the_user_the_answer_or_the_audit_row(self):
        self.db.begin_nested.return_value.__exit__.return_value = False  # let the error reach the handler
        with patch.object(ConversationRepository, "window", return_value=(CONVERSATION, [], None)), patch.object(
            ConversationRepository, "add_exchange", side_effect=RuntimeError("fk_tenant_user")
        ):
            response, _ = self._ask(page="/segments")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], DOCS_ANSWER["answer"])
        self.assertIsInstance(self.db.add.call_args.args[0], SysAuditLog)
        self.db.commit.assert_called_once()
        self.db.rollback.assert_not_called()

    def test_the_audit_row_links_to_the_conversation_and_keeps_the_rewrite_masked(self):
        docs = {**DOCS_ANSWER, "rewritten": "profile scope for linh@example.com", "follows_up": True}
        self._ask_in_chat(docs=docs, question="Q?")
        data = self.db.add.call_args.args[0].after_data
        self.assertEqual(
            (data["conversation_id"], data["history_messages"], data["follows_up"]), (str(CONVERSATION), 2, True)
        )
        self.assertEqual(data["rewritten"], "profile scope for [email]")

    def test_without_a_user_nothing_is_remembered(self):
        app = FastAPI()

        @app.middleware("http")
        async def _api_key(request, call_next):
            request.state.tenant_id = str(TENANT)
            return await call_next(request)

        app.include_router(assistant_api.assistant_router)
        app.dependency_overrides[get_db] = lambda: self.db
        with patch.object(ConversationRepository, "window") as win, patch.object(
            ConversationRepository, "add_exchange"
        ) as add, patch("core.routers.assistant_api.ask_docs", return_value=DOCS_ANSWER):
            response = TestClient(app).post("/assistant/ask", json={"question": "Q?", "conversation_id": str(CONVERSATION)})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["conversation_id"])
        win.assert_not_called()
        add.assert_not_called()

    def test_an_id_page_keys_the_chat_by_its_entity_id(self):
        entity = uuid.uuid4()
        with patch.object(ConversationRepository, "window", return_value=(CONVERSATION, [], None)) as win, patch.object(
            ConversationRepository, "add_exchange"
        ) as add:
            response, _ = self._ask(
                page="/reports/:id", entity_id=str(entity), conversation_id=str(CONVERSATION)
            )

        self.assertEqual(response.status_code, 200)
        win.assert_called_once_with(TENANT, USER, CONVERSATION, "/reports/:id", entity)
        self.assertEqual(add.call_args.args[4], entity)

    def test_the_latest_chat_on_this_page_and_customer_can_be_restored(self):
        rows = [
            SimpleNamespace(role="user", message_text="Q?", status=None, clarify=None, sources=[], created_at=datetime(2026, 10, 6)),
            SimpleNamespace(
                role="assistant", message_text="A.", status="answered", clarify=None,
                sources=[{"path": "x.md", "title": "X", "heading": "X"}], created_at=datetime(2026, 10, 6),
            ),
        ]
        with patch.object(ConversationRepository, "latest", return_value=(CONVERSATION, rows)) as latest:
            response = self.client.get("/assistant/conversation", params={"page": "/profiles/:id", "master_profile_id": str(PROFILE)})

        latest.assert_called_once_with(TENANT, USER, "/profiles/:id", PROFILE)
        data = response.json()
        self.assertEqual(data["conversation_id"], str(CONVERSATION))
        self.assertEqual([m["role"] for m in data["messages"]], ["user", "assistant"])
        self.assertEqual(data["messages"][1]["sources"][0]["path"], "x.md")

    def test_the_latest_chat_can_be_restored_by_entity_id(self):
        entity = uuid.uuid4()
        with patch.object(ConversationRepository, "latest", return_value=(CONVERSATION, [])) as latest:
            response = self.client.get(
                "/assistant/conversation", params={"page": "/reports/:id", "entity_id": str(entity)}
            )

        latest.assert_called_once_with(TENANT, USER, "/reports/:id", entity)
        self.assertEqual(response.json()["conversation_id"], str(CONVERSATION))

    def test_restore_rejects_a_bad_page(self):
        self.assertEqual(self.client.get("/assistant/conversation", params={"page": "https://evil.example"}).status_code, 422)


def _row(role, text, page="/profiles/:id", profile=PROFILE, clarify=None, summary=None):
    return SimpleNamespace(role=role, message_text=text, page=page, master_profile_id=profile, clarify=clarify, summary=summary)


class ConversationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.session = MagicMock()
        self.repo = ConversationRepository(self.session)

    def _window(self, rows, conversation_id=CONVERSATION, page="/profiles/:id", profile=PROFILE):
        self.session.scalars.return_value = rows  # newest first, as the query returns them
        return self.repo.window(TENANT, USER, conversation_id, page, profile)

    def test_the_window_is_oldest_first_cut_short_and_flags_clarifying_replies(self):
        rows = [_row("assistant", "A" * 900, clarify="question"), _row("user", "Q?")]
        cid, history, _ = self._window(rows)

        self.assertEqual(cid, CONVERSATION)
        self.assertEqual([h["role"] for h in history], ["user", "assistant"])
        self.assertEqual((len(history[1]["text"]), history[0]["clarify"], history[1]["clarify"]), (900, False, True))

    def test_the_window_returns_the_newest_assistant_summary(self):
        rows = [_row("assistant", "A2", summary="new"), _row("user", "Q2"), _row("assistant", "A1", summary="old"), _row("user", "Q1")]
        self.assertEqual(self._window(rows)[2], "new")
        self.assertIsNone(self._window([_row("user", "Q?")])[2])

    def test_a_chat_is_not_continued_on_another_page_or_customer(self):
        for page, profile in (("/segments", PROFILE), ("/profiles/:id", uuid.uuid4()), ("/profiles/:id", None)):
            cid, history, _ = self._window([_row("user", "Q?")], page=page, profile=profile)
            self.assertNotEqual(cid, CONVERSATION)
            self.assertEqual(history, [])

    def test_an_unknown_expired_or_foreign_id_starts_a_new_chat_indistinguishably(self):
        cid, history, _ = self._window([])  # no rows for this tenant+user+id
        self.assertNotEqual(cid, CONVERSATION)
        self.assertEqual(history, [])

    def test_without_an_id_a_new_chat_starts_and_nothing_is_read(self):
        cid, history, summary = self.repo.window(TENANT, USER, None, "/segments", None)
        self.assertEqual(history, [])
        self.session.scalars.assert_not_called()

    def test_every_read_is_limited_to_the_tenant_and_the_user_within_30_days(self):
        self._window([])
        statement = self.session.scalars.call_args.args[0].compile()
        self.assertEqual({TENANT, USER, CONVERSATION}, {v for v in statement.params.values() if isinstance(v, uuid.UUID)})
        self.assertIn("created_at >", str(statement))

        self.session.scalar.return_value = None
        self.repo.latest(TENANT, USER, None, None)
        latest = self.session.scalar.call_args.args[0].compile()
        self.assertEqual({TENANT, USER}, {v for v in latest.params.values() if isinstance(v, uuid.UUID)})
        self.assertIn("page IS NULL", str(latest))

    def test_an_exchange_is_two_ordered_rows_plus_a_tenant_scoped_purge_of_old_rows(self):
        self.repo.add_exchange(TENANT, USER, CONVERSATION, "/segments", None, "Q?", "A.", "answered", None, [])

        roles = [call.args[0].role for call in self.session.add.call_args_list]
        self.assertEqual(roles, ["user", "assistant"])
        self.session.flush.assert_called_once()
        self.repo.add_exchange(TENANT, USER, CONVERSATION, "/segments", None, "Q?", "A.", "answered", None, [], "Sum.")
        self.assertEqual(self.session.add.call_args_list[-1].args[0].summary, "Sum.")
        purge = self.session.execute.call_args.args[0].compile()
        self.assertTrue(str(purge).startswith("DELETE FROM customer360.sys_assistant_message"))
        self.assertIn(TENANT, purge.params.values())


if __name__ == "__main__":
    unittest.main()

