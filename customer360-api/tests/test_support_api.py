"""Unit tests for ``POST /support/ask`` and the identity normalization behind it."""

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from sqlalchemy.exc import IntegrityError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.support_repository import (
    InvalidIdentityError,
    SupportRepository,
    normalize_identity,
)
from core.routers import support_api

TENANT = uuid.uuid4()
USER = uuid.uuid4()
PROFILE = uuid.uuid4()
DOCS_ANSWER = {
    "answer": "The reset link is valid for 30 minutes [Reset your password].",
    "found": True,
    "status": "answered",
    "missing": [],
    "clarify": None,
    "sources": [{"path": "reset-password.md", "title": "Reset your password", "heading": "Reset your password"}],
}


class NormalizeIdentityTests(unittest.TestCase):
    def test_email_is_trimmed_and_lowercased(self):
        self.assertEqual(normalize_identity("  Mixed.Case@Example.TEST "), ("email", "mixed.case@example.test"))

    def test_vietnamese_phone_formats_normalize_to_the_same_number(self):
        for raw in ("0901234567", "+84901234567", "090 123 4567", "090-123-4567", "(84) 90 123 4567"):
            self.assertEqual(normalize_identity(raw), ("phone", "0901234567"), raw)

    def test_other_numbers_keep_their_digits(self):
        self.assertEqual(normalize_identity("+1 415 555 0100"), ("phone", "14155550100"))

    def test_garbage_is_rejected(self):
        for raw in ("", "   ", "a@", "not an id", "12", "bao nhieu", "ba@x"):
            with self.assertRaises(InvalidIdentityError, msg=raw):
                normalize_identity(raw)


class AskDocsBodyTests(unittest.TestCase):
    def _body(self, **kwargs):
        response = MagicMock()
        response.json.return_value = DOCS_ANSWER
        with patch("core.routers.support_api.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.return_value = response
            support_api.ask_docs("Q?", **kwargs)
        post = client.return_value.__enter__.return_value.post
        return post.call_args.kwargs["json"]

    def test_view_and_dialog_are_added_only_when_present(self):
        self.assertEqual(self._body(), {"question": "Q?"})
        self.assertEqual(self._body(view="Timeline"), {"question": "Q?", "view": "Timeline"})
        self.assertEqual(self._body(dialog="Add Data Source"), {"question": "Q?", "dialog": "Add Data Source"})

    def test_context_title_is_added_only_when_present(self):
        self.assertEqual(self._body(context=["facts"], context_title="Segment on screen"),
                         {"question": "Q?", "context": ["facts"], "context_title": "Segment on screen"})
        self.assertEqual(self._body(context=["facts"]), {"question": "Q?", "context": ["facts"]})

    def test_page_context_view_and_dialog_all_travel_together(self):
        body = self._body(page="/segments/:id", context=["facts"], view="Tab", dialog="Dialog")
        self.assertEqual(
            body,
            {"question": "Q?", "page": "/segments/:id", "context": ["facts"], "view": "Tab", "dialog": "Dialog"},
        )


class SupportRepositoryTests(unittest.TestCase):
    def test_lookup_is_tenant_scoped_active_only_and_passes_normalized_values(self):
        session = MagicMock()
        session.execute.return_value = [(PROFILE,)]

        ids = SupportRepository(session).find_master_profile_ids(TENANT, "+84 901 234 567")

        sql, params = session.execute.call_args.args[0], session.execute.call_args.args[1]
        self.assertEqual(ids, [PROFILE])
        self.assertEqual(params, {"tenant_id": str(TENANT), "phone": "0901234567"})
        text = str(sql)
        self.assertIn('"customer360".cdp_master_profiles m', text)  # qualified like the ORM models
        self.assertIn("m.tenant_id = CAST(:tenant_id AS uuid)", text)
        self.assertIn("m.status_code = 1", text)
        self.assertIn("LIMIT 2", text)
        self.assertNotIn("ILIKE", text.upper())  # exact match, never a substring search

    def test_email_lookup_uses_the_email_parameter(self):
        session = MagicMock()
        session.execute.return_value = []
        SupportRepository(session).find_master_profile_ids(TENANT, "A@X.com")
        self.assertEqual(session.execute.call_args.args[1], {"tenant_id": str(TENANT), "email": "a@x.com"})


class SupportAskTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()

        @self.app.middleware("http")
        async def _identity(request, call_next):
            request.state.tenant_id = str(TENANT)
            request.state.user_id = str(USER)
            return await call_next(request)

        self.app.include_router(support_api.support_router)
        self.app.dependency_overrides[get_db] = lambda: MagicMock()
        self.client = TestClient(self.app)
        self.created = []

        def fake_create(_self, payload):
            self.created.append(payload)
            return SimpleNamespace(contact_id=uuid.uuid4())

        for target, new in (
            ("core.routers.support_api.RelationsRepository.create_customer_contact", fake_create),
        ):
            patcher = patch(target, new)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _ask(self, docs=None, profiles=(), **body):
        with patch("core.routers.support_api.ask_docs", return_value=docs or DOCS_ANSWER) as ask, patch.object(
            SupportRepository, "find_master_profile_ids", return_value=list(profiles)
        ) as find:
            response = self.client.post("/support/ask", json={"question": "How long is the link valid?", **body})
        return response, ask, find

    def test_kb_hit_for_a_known_customer_logs_the_answer(self):
        response, ask, find = self._ask(profiles=[PROFILE], identity="a@x.com")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual((data["identity"], data["found"], data["status"]), ("matched", True, "answered"))
        self.assertIsNotNone(data["contact_id"])
        self.assertEqual(
            self.created,
            [
                {
                    "tenant_id": TENANT,
                    "user_id": USER,
                    "master_profile_id": PROFILE,
                    "contact_type": "support",
                    "contact_channel": "chat",
                    "contact_content": DOCS_ANSWER["answer"],
                }
            ],
        )
        ask.assert_called_once_with("How long is the link valid?")
        find.assert_called_once_with(TENANT, "a@x.com")

    def test_a_user_from_another_tenant_is_logged_as_no_user_not_a_500(self):
        calls = []

        def fail_once(_self, payload):
            calls.append(payload["user_id"])
            if payload["user_id"] is not None:
                raise IntegrityError("insert", {}, Exception("fk_tenant_user"))
            return SimpleNamespace(contact_id=uuid.uuid4())

        with patch("core.routers.support_api.RelationsRepository.create_customer_contact", fail_once):
            response, _, _ = self._ask(profiles=[PROFILE], identity="a@x.com")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["contact_id"])
        self.assertEqual(calls, [USER, None])

    def test_a_failing_log_never_costs_the_answer(self):
        def always_fail(_self, payload):
            raise IntegrityError("insert", {}, Exception("boom"))

        with patch("core.routers.support_api.RelationsRepository.create_customer_contact", always_fail):
            response, _, _ = self._ask(profiles=[PROFILE], identity="a@x.com")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], DOCS_ANSWER["answer"])
        self.assertIsNone(response.json()["contact_id"])

    def test_the_tenant_comes_from_the_session_not_the_request_body(self):
        other = str(uuid.uuid4())
        _, _, find = self._ask(profiles=[PROFILE], identity="a@x.com", tenant_id=other)
        self.assertEqual(find.call_args.args[0], TENANT)
        self.assertEqual(self.created[0]["tenant_id"], TENANT)

    def test_not_found_answer_is_still_logged_for_a_known_customer(self):
        docs = {**DOCS_ANSWER, "answer": "I don't know — that isn't in the documentation.", "found": False,
                "status": "not_found", "sources": []}
        response, _, _ = self._ask(docs=docs, profiles=[PROFILE], identity="a@x.com")
        self.assertEqual((response.json()["found"], len(self.created)), (False, 1))

    def test_unknown_customer_gets_the_answer_but_nothing_is_inserted(self):
        response, _, _ = self._ask(profiles=[], identity="nobody@x.com")
        data = response.json()
        self.assertEqual((response.status_code, data["identity"], data["contact_id"]), (200, "not_found", None))
        self.assertEqual(data["answer"], DOCS_ANSWER["answer"])
        self.assertEqual(self.created, [])

    def test_ambiguous_match_is_not_logged(self):
        response, _, _ = self._ask(profiles=[PROFILE, uuid.uuid4()], identity="0988776655")
        self.assertEqual((response.json()["identity"], self.created), ("ambiguous", []))

    def test_no_identity_answers_without_a_lookup_or_insert(self):
        response, _, find = self._ask()
        self.assertEqual((response.json()["identity"], self.created), ("not_provided", []))
        find.assert_not_called()

    def test_clarifying_question_is_not_logged(self):
        docs = {**DOCS_ANSWER, "answer": "What limit do you mean?", "found": False, "status": "not_found",
                "clarify": "question", "sources": []}
        response, _, _ = self._ask(docs=docs, profiles=[PROFILE], identity="a@x.com")
        self.assertEqual((response.json()["clarify"], self.created), ("question", []))

    def test_invalid_identity_is_a_422_and_the_docs_service_is_not_called(self):
        with patch("core.routers.support_api.ask_docs") as ask:
            response = self.client.post("/support/ask", json={"question": "Q?", "identity": "not an id"})
        self.assertEqual(response.status_code, 422)
        ask.assert_not_called()

    def test_docs_service_failure_is_a_502_and_nothing_is_logged(self):
        with patch.object(SupportRepository, "find_master_profile_ids", return_value=[PROFILE]), patch(
            "core.routers.support_api.httpx.Client.post", side_effect=httpx.ConnectError("down")
        ):
            response = self.client.post("/support/ask", json={"question": "Q?", "identity": "a@x.com"})
        self.assertEqual((response.status_code, self.created), (502, []))

    def test_empty_or_oversized_question_is_rejected(self):
        for question in ("", "x" * 2001):
            response = self.client.post("/support/ask", json={"question": question})
            self.assertEqual(response.status_code, 422)

    def test_missing_tenant_is_a_400(self):
        bare = FastAPI()
        bare.include_router(support_api.support_router)
        bare.dependency_overrides[get_db] = lambda: MagicMock()
        response = TestClient(bare).post("/support/ask", json={"question": "Q?"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
