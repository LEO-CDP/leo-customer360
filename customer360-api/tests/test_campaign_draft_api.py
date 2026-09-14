"""Unit tests for the /campaigns/draft endpoints (core.routers.campaign_draft_api).

The real CampaignDraftRepository is replaced with an in-memory
FakeCampaignDraftRepository so these tests exercise only HTTP-level wiring
(status codes, request/response schemas) -- no real PostgreSQL instance
required, matching tests/test_campaign_router.py's / test_email_template_api.py's
convention.
"""

import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import get_db
from core.repositories.campaign_draft_repository import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_IN_REVIEW,
    APPROVAL_STATUS_REJECTED,
    CampaignDraftApprovalBlockedError,
    CampaignDraftNotFoundError,
    CampaignDraftValidationError,
    CampaignSegmentNotFoundError,
    CampaignTemplateNotFoundError,
)
from core.routers.campaign_draft_api import router

DEMO_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
DEMO_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _campaign(**overrides) -> SimpleNamespace:
    today = date.today()
    now = datetime.now(timezone.utc)
    defaults = dict(
        campaign_id=uuid.uuid4(),
        tenant_id=DEMO_TENANT_ID,
        status="Draft",
        approval_status=APPROVAL_STATUS_IN_REVIEW,
        segment_id=uuid.uuid4(),
        template_id=uuid.uuid4(),
        name="Q4 Win-Back",
        objective="Win back lapsed customers",
        strategy_summary="Re-engage with a targeted offer",
        ai_plan={"action_plan": ["Send email"]},
        start_date=today + timedelta(days=1),
        end_date=today + timedelta(days=15),
        approved_by=None,
        approved_at=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _content_item_row(content_item_id, position=1, role="primary"):
    return {
        "content_item_id": content_item_id,
        "position": position,
        "role": role,
        "title": "Some Content",
        "item_type": "article",
        "cta_url": "https://example.com",
    }


class FakeCampaignDraftRepository:
    """In-memory stand-in for CampaignDraftRepository -- no real DB needed."""

    campaigns: dict[uuid.UUID, SimpleNamespace] = {}
    content_items: dict[uuid.UUID, list[dict]] = {}
    reviews: dict[uuid.UUID, list[dict]] = {}
    create_side_effect: Optional[Exception] = None
    approve_side_effect: Optional[Exception] = None
    last_create_kwargs: dict[str, Any] = {}

    def __init__(self, session):
        self.session = session

    @classmethod
    def reset(cls):
        cls.campaigns = {}
        cls.content_items = {}
        cls.reviews = {}
        cls.create_side_effect = None
        cls.approve_side_effect = None
        cls.last_create_kwargs = {}

    def create_draft(self, **kwargs):
        FakeCampaignDraftRepository.last_create_kwargs = kwargs
        if FakeCampaignDraftRepository.create_side_effect is not None:
            raise FakeCampaignDraftRepository.create_side_effect

        campaign = _campaign(tenant_id=kwargs["tenant_id"], segment_id=kwargs["segment_id"], template_id=kwargs["template_id"])
        FakeCampaignDraftRepository.campaigns[campaign.campaign_id] = campaign
        FakeCampaignDraftRepository.content_items[campaign.campaign_id] = [
            _content_item_row(uuid.uuid4(), position=1, role="primary")
        ]
        return campaign

    def get_campaign(self, tenant_id, campaign_id):
        campaign = FakeCampaignDraftRepository.campaigns.get(campaign_id)
        if campaign is None or campaign.tenant_id != tenant_id:
            raise CampaignDraftNotFoundError(f"Campaign '{campaign_id}' not found")
        return campaign

    def list_campaign_content_items(self, tenant_id, campaign_id):
        return FakeCampaignDraftRepository.content_items.get(campaign_id, [])

    def edit_draft(self, tenant_id, campaign_id, editor_id, **fields):
        campaign = self.get_campaign(tenant_id, campaign_id)
        for key in ("objective", "strategy_summary", "start_date", "end_date"):
            if fields.get(key) is not None:
                setattr(campaign, key, fields[key])
        if fields.get("content_items") is not None:
            FakeCampaignDraftRepository.content_items[campaign_id] = [
                _content_item_row(item["content_item_id"], position=item.get("position", i + 1), role=item.get("role"))
                for i, item in enumerate(fields["content_items"])
            ]
        if campaign.approval_status in (APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED):
            campaign.approval_status = APPROVAL_STATUS_IN_REVIEW
        return campaign

    def approve(self, tenant_id, campaign_id, reviewer_id):
        if FakeCampaignDraftRepository.approve_side_effect is not None:
            raise FakeCampaignDraftRepository.approve_side_effect
        campaign = self.get_campaign(tenant_id, campaign_id)
        campaign.approval_status = APPROVAL_STATUS_APPROVED
        campaign.approved_by = reviewer_id
        campaign.approved_at = datetime.now(timezone.utc)
        return campaign

    def reject(self, tenant_id, campaign_id, reviewer_id, reason=None):
        campaign = self.get_campaign(tenant_id, campaign_id)
        campaign.approval_status = APPROVAL_STATUS_REJECTED
        return campaign

    def list_campaign_history(self, tenant_id, campaign_id):
        return FakeCampaignDraftRepository.reviews.get(campaign_id, [])


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def _inject_identity(request: Request, call_next):
        request.state.tenant_id = request.headers.get("X-Tenant-Id", str(DEMO_TENANT_ID))
        request.state.user_id = request.headers.get("X-User-Id", str(DEMO_USER_ID))
        return await call_next(request)

    app.dependency_overrides[get_db] = lambda: None
    return app


class GenerateCampaignDraftTests(unittest.TestCase):
    def setUp(self):
        FakeCampaignDraftRepository.reset()
        self._repo_patcher = patch(
            "core.routers.campaign_draft_api.CampaignDraftRepository", FakeCampaignDraftRepository
        )
        self._repo_patcher.start()
        self.addCleanup(self._repo_patcher.stop)
        self.client = TestClient(_build_test_app())

    def _payload(self, **overrides) -> dict[str, Any]:
        payload = {
            "segment_id": str(uuid.uuid4()),
            "template_id": str(uuid.uuid4()),
            "objective": "Drive Q4 repeat purchases among lapsed VIP customers",
            "budget_time_constraints": "Launch within 2 weeks",
        }
        payload.update(overrides)
        return payload

    def test_generate_returns_201_with_draft_campaign(self):
        response = self.client.post("/campaigns/draft", json=self._payload())

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "Draft")
        self.assertEqual(body["approval_status"], "InReview")
        self.assertTrue(len(body["content_items"]) > 0)

    def test_generate_missing_required_objective_returns_422(self):
        payload = self._payload()
        del payload["objective"]

        response = self.client.post("/campaigns/draft", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(FakeCampaignDraftRepository.campaigns, {})

    def test_generate_refused_for_unresolvable_segment_returns_409(self):
        FakeCampaignDraftRepository.create_side_effect = CampaignDraftValidationError("Segment is not resolvable")

        response = self.client.post("/campaigns/draft", json=self._payload())

        self.assertEqual(response.status_code, 409)
        self.assertEqual(FakeCampaignDraftRepository.campaigns, {})

    def test_generate_refused_for_missing_segment_returns_404(self):
        FakeCampaignDraftRepository.create_side_effect = CampaignSegmentNotFoundError("Segment not found")

        response = self.client.post("/campaigns/draft", json=self._payload())

        self.assertEqual(response.status_code, 404)

    def test_generate_refused_for_template_not_approved_returns_409(self):
        FakeCampaignDraftRepository.create_side_effect = CampaignDraftValidationError("Template is not Approved")

        response = self.client.post("/campaigns/draft", json=self._payload())

        self.assertEqual(response.status_code, 409)

    def test_generate_refused_for_missing_template_returns_404(self):
        FakeCampaignDraftRepository.create_side_effect = CampaignTemplateNotFoundError("Template not found")

        response = self.client.post("/campaigns/draft", json=self._payload())

        self.assertEqual(response.status_code, 404)

    def test_list_campaign_content_items(self):
        created = self.client.post("/campaigns/draft", json=self._payload()).json()

        response = self.client.get(f"/campaigns/{created['campaign_id']}/content-items")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.json()) > 0)

    def test_content_items_for_nonexistent_campaign_returns_404(self):
        response = self.client.get(f"/campaigns/{uuid.uuid4()}/content-items")

        self.assertEqual(response.status_code, 404)


class ReviewCampaignDraftTests(unittest.TestCase):
    def setUp(self):
        FakeCampaignDraftRepository.reset()
        self._repo_patcher = patch(
            "core.routers.campaign_draft_api.CampaignDraftRepository", FakeCampaignDraftRepository
        )
        self._repo_patcher.start()
        self.addCleanup(self._repo_patcher.stop)
        self.client = TestClient(_build_test_app())

        created = self.client.post(
            "/campaigns/draft",
            json={
                "segment_id": str(uuid.uuid4()),
                "template_id": str(uuid.uuid4()),
                "objective": "Win back lapsed customers",
            },
        ).json()
        self.campaign_id = created["campaign_id"]

    def test_approve_without_changes_records_reviewer_identity(self):
        response = self.client.post(f"/campaigns/{self.campaign_id}/approve")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["approval_status"], "Approved")
        self.assertEqual(body["approved_by"], str(DEMO_USER_ID))
        self.assertIsNotNone(body["approved_at"])

    def test_edit_then_approve_reflects_edited_values(self):
        self.client.patch(f"/campaigns/{self.campaign_id}/draft", json={"objective": "Revised objective"})
        response = self.client.post(f"/campaigns/{self.campaign_id}/approve")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["objective"], "Revised objective")
        self.assertEqual(body["approval_status"], "Approved")

    def test_reject_sets_approval_status_rejected(self):
        response = self.client.post(f"/campaigns/{self.campaign_id}/reject", json={"reason": "Budget too aggressive"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["approval_status"], "Rejected")

    def test_editing_approved_campaign_reverts_to_in_review(self):
        self.client.post(f"/campaigns/{self.campaign_id}/approve")

        response = self.client.patch(f"/campaigns/{self.campaign_id}/draft", json={"objective": "Post-approval edit"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["approval_status"], "InReview")

    def test_rejecting_then_editing_resubmits_to_in_review(self):
        self.client.post(f"/campaigns/{self.campaign_id}/reject", json={})

        response = self.client.patch(
            f"/campaigns/{self.campaign_id}/draft", json={"start_date": "2026-11-01", "end_date": "2026-11-15"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["approval_status"], "InReview")

    def test_approve_blocked_409_when_linked_template_not_approved(self):
        FakeCampaignDraftRepository.approve_side_effect = CampaignDraftApprovalBlockedError(
            "Linked template is not Approved"
        )

        response = self.client.post(f"/campaigns/{self.campaign_id}/approve")

        self.assertEqual(response.status_code, 409)
        self.assertIn("template", response.json()["detail"].lower())

    def test_approve_blocked_409_naming_archived_content_item(self):
        archived_id = str(uuid.uuid4())
        FakeCampaignDraftRepository.approve_side_effect = CampaignDraftApprovalBlockedError(
            f"Content item(s) no longer available: {archived_id}"
        )

        response = self.client.post(f"/campaigns/{self.campaign_id}/approve")

        self.assertEqual(response.status_code, 409)
        self.assertIn(archived_id, response.json()["detail"])

    def test_history_endpoint_reachable_after_approve(self):
        self.client.post(f"/campaigns/{self.campaign_id}/approve")

        response = self.client.get(f"/campaigns/{self.campaign_id}/history")

        self.assertEqual(response.status_code, 200)


class CampaignDraftTenantIsolationTests(unittest.TestCase):
    """Mirrors tests/test_multi_tenant_isolation.py's convention: a user in
    tenant A must never see or edit tenant B's campaign drafts."""

    TENANT_A = str(uuid.uuid4())
    TENANT_B = str(uuid.uuid4())

    def setUp(self):
        FakeCampaignDraftRepository.reset()
        self._repo_patcher = patch(
            "core.routers.campaign_draft_api.CampaignDraftRepository", FakeCampaignDraftRepository
        )
        self._repo_patcher.start()
        self.addCleanup(self._repo_patcher.stop)
        self.client = TestClient(_build_test_app())

        created = self.client.post(
            "/campaigns/draft",
            json={
                "segment_id": str(uuid.uuid4()),
                "template_id": str(uuid.uuid4()),
                "objective": "Tenant A's campaign",
            },
            headers={"X-Tenant-Id": self.TENANT_A},
        ).json()
        self.tenant_a_campaign_id = created["campaign_id"]

    def test_tenant_b_cannot_view_content_items(self):
        response = self.client.get(
            f"/campaigns/{self.tenant_a_campaign_id}/content-items", headers={"X-Tenant-Id": self.TENANT_B}
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_b_cannot_edit_draft(self):
        response = self.client.patch(
            f"/campaigns/{self.tenant_a_campaign_id}/draft",
            json={"objective": "Hijacked"},
            headers={"X-Tenant-Id": self.TENANT_B},
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_b_cannot_approve(self):
        response = self.client.post(
            f"/campaigns/{self.tenant_a_campaign_id}/approve", headers={"X-Tenant-Id": self.TENANT_B}
        )
        self.assertEqual(response.status_code, 404)

    def test_tenant_b_cannot_view_history(self):
        response = self.client.get(
            f"/campaigns/{self.tenant_a_campaign_id}/history", headers={"X-Tenant-Id": self.TENANT_B}
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
