"""Unit tests for POST /segments/from-description (core.routers.segment_api).

Real router, patched repository; a middleware sets request.state.tenant_id like core/auth.py.
"""

import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.segment_draft_repository import (
    SegmentDraftGenerationError,
    SegmentDraftRepository,
    SegmentDraftValidationError,
)
from core.routers.segment_api import segments_router
from leo_customer360_dao.schemas.segmentation import SegmentDraftResult

MODULE = "core.routers.segment_api"
TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
URL = "/segments/from-description"

VALID = SegmentDraftResult(
    validation_status="valid",
    ready_for_segment_persistence=True,
    interpretation="Interpreted 'make' as 'male'.",
    json_rules={"condition": "AND", "rules": [
        {"id": "gender", "field": "gender", "type": "string", "input": "text", "operator": "equal", "value": "male"}]},
    segment_tag="male_customers",
    segment_name="Male Customers",
    fields_used=["gender"],
)


def _app(tenant=TENANT):
    app = FastAPI()

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if tenant is not None:
            request.state.tenant_id = str(tenant)
        return await call_next(request)

    app.include_router(segments_router)
    app.dependency_overrides[get_db] = lambda: MagicMock(name="session")
    return app


class FromDescriptionRouterTests(unittest.TestCase):
    def setUp(self):
        self.repo = MagicMock(spec=SegmentDraftRepository)
        self.repo.draft_from_description.return_value = VALID
        patcher = patch(f"{MODULE}.SegmentDraftRepository", return_value=self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)
        domain_patcher = patch(f"{MODULE}.validate_domain_value")
        self.validate_domain = domain_patcher.start()
        self.addCleanup(domain_patcher.stop)
        self.client = TestClient(_app())

    def test_valid_result_matches_the_spec_output_contract(self):
        resp = self.client.post(URL, json={"description": "gender is make"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        for key in ("interpretation", "json_rules", "validation_status", "ready_for_segment_persistence"):
            self.assertIn(key, body)
        self.assertEqual(body["validation_status"], "valid")
        self.assertNotIn("sql_rules", body)
        self.assertNotIn("estimated_count", body)

    def test_domain_defaults_to_all(self):
        self.client.post(URL, json={"description": "x"})
        self.assertEqual(self.repo.draft_from_description.call_args.args[2], "all")
        self.assertEqual(self.validate_domain.call_args.kwargs, {"allow_all": True})

    def test_questions_and_rejections_are_200_results(self):
        for status in ("needs_clarification", "rejected"):
            self.repo.draft_from_description.return_value = SegmentDraftResult(
                validation_status=status, ready_for_segment_persistence=False,
                question="Which did you mean: male, female, other?", suggestions=["male", "female", "other"])
            resp = self.client.post(URL, json={"description": "gender is m"})
            self.assertEqual(resp.status_code, 200, status)
            self.assertEqual(resp.json()["validation_status"], status)
            self.assertFalse(resp.json()["ready_for_segment_persistence"])

    def test_current_rules_are_passed_through(self):
        rules = {"condition": "AND", "rules": [{"field": "gender", "operator": "equal", "value": "male"}]}
        resp = self.client.post(URL, json={"description": "also only VIPs", "current_rules": rules})
        self.assertEqual(resp.status_code, 200, resp.text)
        kwargs = self.repo.draft_from_description.call_args.kwargs
        self.assertEqual(kwargs["current_rules"], rules)
        self.assertNotIn("history", kwargs)

    def test_so_far_and_last_question_are_passed_through(self):
        rules = {"condition": "AND", "rules": [{"field": "gender", "operator": "equal", "value": "male"}]}
        resp = self.client.post(URL, json={
            "description": "male",
            "so_far": "Wants VIP customers",
            "last_question": "Which gender?",
            "current_rules": rules,
        })
        self.assertEqual(resp.status_code, 200, resp.text)
        kwargs = self.repo.draft_from_description.call_args.kwargs
        self.assertEqual(kwargs["so_far"], "Wants VIP customers")
        self.assertEqual(kwargs["last_question"], "Which gender?")

    def test_so_far_response_field_is_returned(self):
        self.repo.draft_from_description.return_value = SegmentDraftResult(
            validation_status="valid", ready_for_segment_persistence=True,
            interpretation="ok", so_far="Wants VIP male customers")
        resp = self.client.post(URL, json={"description": "male", "so_far": "Wants VIP customers"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["so_far"], "Wants VIP male customers")

    def test_overlong_so_far_or_last_question_is_422_without_calling_the_repository(self):
        for field in ("so_far", "last_question"):
            resp = self.client.post(URL, json={"description": "x", field: "y" * 2001})
            self.assertEqual(resp.status_code, 422, field)
        self.repo.draft_from_description.assert_not_called()

    def test_literal_route_is_not_shadowed_by_item_routes(self):
        paths = [r.path for r in segments_router.routes]
        self.assertLess(paths.index(URL), paths.index("/segments/{item_id}"))

    def test_tenant_comes_from_auth_not_the_body(self):
        resp = self.client.post(URL, json={"description": "x", "tenant_id": str(OTHER_TENANT)})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.repo.draft_from_description.call_args.args[0], TENANT)

    def test_no_tenant_context_is_refused_before_any_work(self):
        resp = TestClient(_app(tenant=None)).post(URL, json={"description": "x"})
        self.assertEqual(resp.status_code, 400)
        self.repo.draft_from_description.assert_not_called()

    def test_validation_error_is_422(self):
        self.repo.draft_from_description.side_effect = SegmentDraftValidationError("no attributes")
        resp = self.client.post(URL, json={"description": "x"})
        self.assertEqual((resp.status_code, resp.json()["detail"]), (422, "no attributes"))

    def test_agent_failure_is_502(self):
        self.repo.draft_from_description.side_effect = SegmentDraftGenerationError("agent down")
        self.assertEqual(self.client.post(URL, json={"description": "x"}).status_code, 502)

    def test_unknown_domain_is_422_without_calling_the_model(self):
        self.validate_domain.side_effect = ValueError("domain 'nope' is not enabled")
        resp = self.client.post(URL, json={"description": "x", "domain": "nope"})
        self.assertEqual(resp.status_code, 422)
        self.repo.draft_from_description.assert_not_called()

    def test_empty_and_overlong_descriptions_are_rejected_by_the_schema(self):
        for description in ("", "x" * 2001):
            self.assertEqual(self.client.post(URL, json={"description": description}).status_code, 422)
        self.repo.draft_from_description.assert_not_called()


if __name__ == "__main__":
    unittest.main()
