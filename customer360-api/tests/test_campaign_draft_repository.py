"""Unit tests for core.repositories.campaign_draft_repository.CampaignDraftRepository.

SegmentRepository/CampaignDraftRepository._get_template/generate_campaign_plan are mocked
(no real PostgreSQL/AI-provider calls); a minimal FakeSession stands in for
the SQLAlchemy Session, matching this repo's hermetic-testing convention.
"""

import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from leo_customer360_agent.client import AIProviderError, GeneratedCampaignPlan
from core.repositories.campaign_planner_agent import AgentConfigurationInvalidError, ResolvedPlanner
from core.repositories.campaign_draft_repository import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_IN_REVIEW,
    APPROVAL_STATUS_REJECTED,
    CampaignDraftApprovalBlockedError,
    CampaignDraftActorNotFoundError,
    CampaignDraftConflictError,
    CampaignDraftRepository,
    CampaignDraftStaleError,
    CampaignDraftValidationError,
    CampaignSegmentNotFoundError,
    CampaignTemplateNotFoundError,
)
from leo_customer360_dao.models.system import SysUser

DEMO_TENANT_ID = uuid.uuid4()
DEMO_SEGMENT_ID = uuid.uuid4()
DEMO_TEMPLATE_ID = uuid.uuid4()
CONTENT_ITEM_1 = uuid.uuid4()
CONTENT_ITEM_2 = uuid.uuid4()
AGENT_CODE = "campaign_planner_uat"
FAKE_PLANNER = ResolvedPlanner(
    model="openrouter/openai/gpt-5.6-luna",
    extra_config={"temperature": 0.2},
    instructions="Registry prompt v2",
    snapshot={"agent_code": AGENT_CODE, "instruction_version": 2, "run_at": "2026-10-09T00:00:00+00:00"},
)


def _patch_planner(planner=FAKE_PLANNER, side_effect=None):
    if side_effect is not None:
        return patch("core.repositories.campaign_draft_repository.resolve_planner", side_effect=side_effect)
    return patch("core.repositories.campaign_draft_repository.resolve_planner", return_value=planner)


def _editable_campaign(**overrides):
    """A campaign stand-in carrying every attribute the governed editor reads."""
    values = dict(
        campaign_id=uuid.uuid4(),
        tenant_id=DEMO_TENANT_ID,
        segment_id=None,
        template_id=None,
        objective="Original objective",
        strategy_summary="Original strategy",
        ai_plan=None,
        start_date=date.today() + timedelta(days=1),
        end_date=date.today() + timedelta(days=10),
        approval_status=APPROVAL_STATUS_IN_REVIEW,
        approved_by=None,
        approved_at=None,
        metadata_=None,
        updated_at=None,
        campaign_code=None,
        name="Campaign",
        status="Draft",
        channel=None,
        platform=None,
        description=None,
        keywords=None,
        lang="en",
        budget_amount=None,
        currency="VND",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeScalarsResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def first(self):
        # Used by the optimistic-concurrency guard's own SELECT; returning
        # None means "no conflict" in these tests (no real concurrent writer).
        return None

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None


class FakeSession:
    """Minimal SQLAlchemy Session stand-in: execute() always returns the
    scripted candidate content items (the only SELECT create_draft issues
    directly; segment/template resolution is mocked at the repository
    level, not via session.execute)."""

    def __init__(self, candidate_content_items=None):
        self._candidate_content_items = candidate_content_items or []
        self.audit_user_exists = True
        self.added: list = []
        self.committed = False
        self.flushed = False

    def execute(self, statement):
        descriptions = statement.column_descriptions
        if descriptions and descriptions[0]["entity"] is SysUser:
            return _FakeScalarsResult([uuid.uuid4()] if self.audit_user_exists else [])
        return _FakeScalarsResult(self._candidate_content_items)

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append(obj)

    def flush(self):
        self.flushed = True
        for obj in self.added:
            if getattr(obj, "campaign_id", "MISSING") is None:
                obj.campaign_id = uuid.uuid4()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def refresh(self, _obj):
        pass


def _fake_content_item(content_item_id, title="Item", item_type="article", status_code=1, segment_tags=None, summary=""):
    return SimpleNamespace(
        content_item_id=content_item_id,
        title=title,
        item_type=item_type,
        status_code=status_code,
        segment_tags=segment_tags or [],
        summary=summary,
    )


def _fake_segment(is_active=True, status_code=1, segment_tag="vip_lapsed"):
    return SimpleNamespace(
        segment_id=DEMO_SEGMENT_ID,
        segment_name="Lapsed VIPs",
        segment_tag=segment_tag,
        is_active=is_active,
        status_code=status_code,
    )


def _fake_template(status="Approved"):
    return SimpleNamespace(template_id=DEMO_TEMPLATE_ID, status=status)


def _valid_generated_plan(content_item_ids=None) -> GeneratedCampaignPlan:
    today = date.today()
    return GeneratedCampaignPlan(
        name="Q4 Win-Back",
        objective="Win back lapsed customers",
        strategy_summary="Re-engage with a targeted offer",
        action_plan=["Send email", "Follow up in 3 days"],
        start_date=today + timedelta(days=1),
        end_date=today + timedelta(days=15),
        content_item_ids=content_item_ids if content_item_ids is not None else [str(CONTENT_ITEM_1)],
    )


class CreateDraftTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession(
            candidate_content_items=[
                _fake_content_item(CONTENT_ITEM_1, segment_tags=["vip_lapsed"]),
                _fake_content_item(CONTENT_ITEM_2, segment_tags=["vip_lapsed"]),
            ]
        )
        self.repo = CampaignDraftRepository(self.session)
        planner_patcher = _patch_planner()
        planner_patcher.start()
        self.addCleanup(planner_patcher.stop)

    def _patch_segment(self, segment):
        return patch(
            "core.repositories.campaign_draft_repository.SegmentRepository.get_segment", return_value=segment
        )

    def _patch_template(self, template=None, side_effect=None):
        if side_effect is not None:
            return patch.object(CampaignDraftRepository, "_get_template", side_effect=side_effect)
        return patch.object(CampaignDraftRepository, "_get_template", return_value=template)

    def _patch_generate(self, plan=None, side_effect=None):
        if side_effect is not None:
            return patch("core.repositories.campaign_draft_repository.generate_campaign_plan", side_effect=side_effect)
        return patch("core.repositories.campaign_draft_repository.generate_campaign_plan", return_value=plan)

    def test_create_draft_commits_read_transaction_before_calling_ai(self):
        """Bug fix regression: the DB transaction must not be held open
        across the (up to ~30s) AI HTTP call. Verifies the session is
        already committed -- releasing its pooled connection -- by the time
        generate_campaign_plan() is invoked."""
        committed_before_ai_call = []

        def _capture_and_generate(*_args, **_kwargs):
            committed_before_ai_call.append(self.session.committed)
            return _valid_generated_plan()

        with self._patch_segment(_fake_segment()), self._patch_template(_fake_template()), self._patch_generate(
            side_effect=_capture_and_generate
        ):
            self.repo.create_draft(
                tenant_id=DEMO_TENANT_ID,
                created_by=None,
                segment_id=DEMO_SEGMENT_ID,
                template_id=DEMO_TEMPLATE_ID,
                objective="Objective",
                    agent_code=AGENT_CODE,
            )

        self.assertEqual(committed_before_ai_call, [True])

    def test_create_draft_success_persists_campaign_content_and_audit_row(self):
        with self._patch_segment(_fake_segment()), self._patch_template(_fake_template()), self._patch_generate(
            _valid_generated_plan(content_item_ids=[str(CONTENT_ITEM_1), str(CONTENT_ITEM_2)])
        ):
            campaign = self.repo.create_draft(
                tenant_id=DEMO_TENANT_ID,
                created_by=uuid.uuid4(),
                segment_id=DEMO_SEGMENT_ID,
                template_id=DEMO_TEMPLATE_ID,
                objective="Win back lapsed customers",
                agent_code=AGENT_CODE,
            )

        self.assertEqual(campaign.approval_status, APPROVAL_STATUS_IN_REVIEW)
        self.assertEqual(campaign.status, "Draft")
        self.assertIsNotNone(campaign.ai_plan)
        self.assertTrue(self.session.committed)

        content_item_rows = [obj for obj in self.session.added if type(obj).__name__ == "CampaignContentItem"]
        self.assertEqual(len(content_item_rows), 2)
        audit_rows = [obj for obj in self.session.added if type(obj).__name__ == "SysAuditLog"]
        self.assertEqual(len(audit_rows), 1)
        self.assertEqual(audit_rows[0].action, "CREATE")
        self.assertEqual(audit_rows[0].resource_type, "crm_campaign")

    def test_create_draft_refuses_segment_not_found(self):
        with self._patch_segment(None), self._patch_template(_fake_template()), self._patch_generate(_valid_generated_plan()):
            with self.assertRaises(CampaignSegmentNotFoundError):
                self.repo.create_draft(
                    tenant_id=DEMO_TENANT_ID,
                    created_by=None,
                    segment_id=DEMO_SEGMENT_ID,
                    template_id=DEMO_TEMPLATE_ID,
                    objective="Objective",
                    agent_code=AGENT_CODE,
                )
        self.assertEqual(self.session.added, [])
        self.assertFalse(self.session.committed)

    def test_create_draft_refuses_unresolvable_segment(self):
        with self._patch_segment(_fake_segment(is_active=False)), self._patch_template(_fake_template()), self._patch_generate(
            _valid_generated_plan()
        ):
            with self.assertRaises(CampaignDraftValidationError):
                self.repo.create_draft(
                    tenant_id=DEMO_TENANT_ID,
                    created_by=None,
                    segment_id=DEMO_SEGMENT_ID,
                    template_id=DEMO_TEMPLATE_ID,
                    objective="Objective",
                    agent_code=AGENT_CODE,
                )
        self.assertEqual(self.session.added, [])

    def test_create_draft_refuses_template_not_found(self):
        with self._patch_segment(_fake_segment()), self._patch_template(template=None), self._patch_generate(
            _valid_generated_plan()
        ):
            with self.assertRaises(CampaignTemplateNotFoundError):
                self.repo.create_draft(
                    tenant_id=DEMO_TENANT_ID,
                    created_by=None,
                    segment_id=DEMO_SEGMENT_ID,
                    template_id=DEMO_TEMPLATE_ID,
                    objective="Objective",
                    agent_code=AGENT_CODE,
                )
        self.assertEqual(self.session.added, [])

    def test_create_draft_refuses_template_not_approved(self):
        with self._patch_segment(_fake_segment()), self._patch_template(_fake_template(status="Draft")), self._patch_generate(
            _valid_generated_plan()
        ):
            with self.assertRaises(CampaignDraftValidationError):
                self.repo.create_draft(
                    tenant_id=DEMO_TENANT_ID,
                    created_by=None,
                    segment_id=DEMO_SEGMENT_ID,
                    template_id=DEMO_TEMPLATE_ID,
                    objective="Objective",
                    agent_code=AGENT_CODE,
                )
        self.assertEqual(self.session.added, [])

    def test_create_draft_rejects_invalid_schedule_window(self):
        today = date.today()
        invalid_plan = _valid_generated_plan()
        invalid_plan.start_date = today + timedelta(days=10)
        invalid_plan.end_date = today + timedelta(days=1)  # end before start

        with self._patch_segment(_fake_segment()), self._patch_template(_fake_template()), self._patch_generate(invalid_plan):
            with self.assertRaises(CampaignDraftValidationError):
                self.repo.create_draft(
                    tenant_id=DEMO_TENANT_ID,
                    created_by=None,
                    segment_id=DEMO_SEGMENT_ID,
                    template_id=DEMO_TEMPLATE_ID,
                    objective="Objective",
                    agent_code=AGENT_CODE,
                )
        self.assertEqual(self.session.added, [])

    def test_create_draft_provider_failure_persists_nothing(self):
        with self._patch_segment(_fake_segment()), self._patch_template(_fake_template()), self._patch_generate(
            side_effect=AIProviderError("boom")
        ):
            with self.assertRaises(CampaignDraftValidationError):
                self.repo.create_draft(
                    tenant_id=DEMO_TENANT_ID,
                    created_by=None,
                    segment_id=DEMO_SEGMENT_ID,
                    template_id=DEMO_TEMPLATE_ID,
                    objective="Objective",
                    agent_code=AGENT_CODE,
                )
        self.assertEqual(self.session.added, [])

    def test_create_draft_discards_content_id_not_in_candidate_list(self):
        bogus_id = str(uuid.uuid4())
        with self._patch_segment(_fake_segment()), self._patch_template(_fake_template()), self._patch_generate(
            _valid_generated_plan(content_item_ids=[str(CONTENT_ITEM_1), bogus_id])
        ):
            self.repo.create_draft(
                tenant_id=DEMO_TENANT_ID,
                created_by=None,
                segment_id=DEMO_SEGMENT_ID,
                template_id=DEMO_TEMPLATE_ID,
                objective="Objective",
                    agent_code=AGENT_CODE,
            )

        content_item_rows = [obj for obj in self.session.added if type(obj).__name__ == "CampaignContentItem"]
        persisted_ids = {str(obj.content_item_id) for obj in content_item_rows}
        self.assertEqual(persisted_ids, {str(CONTENT_ITEM_1)})


class ListApprovedTemplatesTests(unittest.TestCase):
    def _repo(self, templates):
        return CampaignDraftRepository(FakeSession(candidate_content_items=templates))

    def test_splits_email_and_zns_templates(self):
        email = SimpleNamespace(template_id=uuid.uuid4(), name="Mail", metadata_=None)
        zns = SimpleNamespace(template_id=uuid.uuid4(), name="Zalo", metadata_={"channel": "zalo_zns"})
        repo = self._repo([email, zns])

        self.assertEqual([t["name"] for t in repo.list_approved_templates(DEMO_TENANT_ID, "email")], ["Mail"])
        self.assertEqual(
            repo.list_approved_templates(DEMO_TENANT_ID, "zalo_zns"),
            [{"template_id": zns.template_id, "name": "Zalo", "channel": "zalo_zns"}],
        )


class ApproveRejectTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.repo = CampaignDraftRepository(self.session)
        self.campaign = SimpleNamespace(
            campaign_id=uuid.uuid4(),
            tenant_id=DEMO_TENANT_ID,
            template_id=DEMO_TEMPLATE_ID,
            approval_status=APPROVAL_STATUS_IN_REVIEW,
            approved_by=None,
            approved_at=None,
            updated_at=None,
        )

    def _patch_get_campaign(self):
        return patch.object(CampaignDraftRepository, "get_campaign", return_value=self.campaign)

    def _patch_content_links(self, links):
        return patch.object(CampaignDraftRepository, "_get_content_item_links", return_value=links)

    def _patch_template(self, template=None, side_effect=None):
        if side_effect is not None:
            return patch.object(CampaignDraftRepository, "_get_template", side_effect=side_effect)
        return patch.object(CampaignDraftRepository, "_get_template", return_value=template)

    def test_approve_success_sets_approved_and_records_review(self):
        with self._patch_get_campaign(), self._patch_content_links([]), self._patch_template(_fake_template()):
            campaign = self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())

        self.assertEqual(campaign.approval_status, APPROVAL_STATUS_APPROVED)
        review_rows = [obj for obj in self.session.added if type(obj).__name__ == "CampaignReview"]
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(review_rows[0].decision, "approve")

    def test_approve_blocked_when_linked_template_not_approved(self):
        with self._patch_get_campaign(), self._patch_content_links([]), self._patch_template(
            _fake_template(status="NeedsReview")
        ):
            with self.assertRaises(CampaignDraftApprovalBlockedError):
                self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())

    def test_approve_blocked_when_content_item_archived(self):
        link = SimpleNamespace(content_item_id=CONTENT_ITEM_1)
        self.session._candidate_content_items = []  # active-items lookup finds nothing -> archived
        with self._patch_get_campaign(), self._patch_content_links([link]), self._patch_template(_fake_template()):
            with self.assertRaises(CampaignDraftApprovalBlockedError) as ctx:
                self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())
        self.assertIn(str(CONTENT_ITEM_1), str(ctx.exception))

    def test_approve_succeeds_when_content_item_still_active(self):
        link = SimpleNamespace(content_item_id=CONTENT_ITEM_1)
        self.session._candidate_content_items = [_fake_content_item(CONTENT_ITEM_1, status_code=1)]
        with self._patch_get_campaign(), self._patch_content_links([link]), self._patch_template(_fake_template()):
            campaign = self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())
        self.assertEqual(campaign.approval_status, APPROVAL_STATUS_APPROVED)

    def test_reject_sets_rejected_and_records_review(self):
        with self._patch_get_campaign():
            campaign = self.repo.reject(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4(), reason="Bad tone")

        self.assertEqual(campaign.approval_status, APPROVAL_STATUS_REJECTED)
        review_rows = [obj for obj in self.session.added if type(obj).__name__ == "CampaignReview"]
        self.assertEqual(review_rows[0].decision, "reject")
        self.assertEqual(review_rows[0].reason, "Bad tone")

    def test_approve_blocked_when_campaign_already_approved(self):
        self.campaign.approval_status = APPROVAL_STATUS_APPROVED
        with self._patch_get_campaign():
            with self.assertRaises(CampaignDraftApprovalBlockedError):
                self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())
        self.assertEqual(self.session.added, [])

    def test_approve_blocked_when_campaign_rejected(self):
        self.campaign.approval_status = APPROVAL_STATUS_REJECTED
        with self._patch_get_campaign():
            with self.assertRaises(CampaignDraftApprovalBlockedError):
                self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())
        self.assertEqual(self.session.added, [])

    def test_reject_blocked_when_campaign_already_rejected(self):
        self.campaign.approval_status = APPROVAL_STATUS_REJECTED
        with self._patch_get_campaign():
            with self.assertRaises(CampaignDraftApprovalBlockedError):
                self.repo.reject(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())
        self.assertEqual(self.session.added, [])

    def test_reject_blocked_when_campaign_already_approved(self):
        self.campaign.approval_status = APPROVAL_STATUS_APPROVED
        with self._patch_get_campaign():
            with self.assertRaises(CampaignDraftApprovalBlockedError):
                self.repo.reject(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())
        self.assertEqual(self.session.added, [])


class CandidateContentItemFilteringTests(unittest.TestCase):
    """US3: candidate list is always drawn from existing inventory, filtered
    by segment_tag/objective overlap, never fabricated."""

    def setUp(self):
        matching = _fake_content_item(CONTENT_ITEM_1, title="Win-back offer")
        matching.segment_tags = ["vip_lapsed"]
        matching.summary = "A special offer"
        non_matching = _fake_content_item(CONTENT_ITEM_2, title="Unrelated article")
        non_matching.segment_tags = ["new_signup"]
        non_matching.summary = "Something else"
        self.session = FakeSession(candidate_content_items=[matching, non_matching])
        self.repo = CampaignDraftRepository(self.session)
        planner_patcher = _patch_planner()
        planner_patcher.start()
        self.addCleanup(planner_patcher.stop)

    def test_filters_by_segment_tag_overlap(self):
        items = self.repo.get_candidate_content_items(DEMO_TENANT_ID, DEMO_SEGMENT_ID, segment_tag="vip_lapsed")
        self.assertEqual([item.content_item_id for item in items], [CONTENT_ITEM_1])

    def test_returns_empty_list_not_fabricated_when_nothing_matches(self):
        items = self.repo.get_candidate_content_items(DEMO_TENANT_ID, DEMO_SEGMENT_ID, segment_tag="no_such_tag", objective="zzz")
        self.assertEqual(items, [])

    def test_no_filter_returns_full_tenant_active_list(self):
        items = self.repo.get_candidate_content_items(DEMO_TENANT_ID, DEMO_SEGMENT_ID)
        self.assertEqual(len(items), 2)

    def test_create_draft_succeeds_with_empty_content_plan_when_no_candidates_match(self):
        self.session._candidate_content_items = []  # no suitable items in inventory
        with patch(
            "core.repositories.campaign_draft_repository.SegmentRepository.get_segment",
            return_value=_fake_segment(),
        ), patch.object(
            CampaignDraftRepository, "_get_template", return_value=_fake_template()
        ), patch(
            "core.repositories.campaign_draft_repository.generate_campaign_plan",
            return_value=_valid_generated_plan(content_item_ids=[]),
        ):
            campaign = self.repo.create_draft(
                tenant_id=DEMO_TENANT_ID,
                created_by=None,
                segment_id=DEMO_SEGMENT_ID,
                template_id=DEMO_TEMPLATE_ID,
                objective="Objective",
                    agent_code=AGENT_CODE,
            )

        self.assertEqual(campaign.approval_status, APPROVAL_STATUS_IN_REVIEW)
        content_item_rows = [obj for obj in self.session.added if type(obj).__name__ == "CampaignContentItem"]
        self.assertEqual(content_item_rows, [])


class ReviewerContentPlanAdjustmentTests(unittest.TestCase):
    """US3: reviewer add/remove/reorder before approval is reflected in the
    approved campaign and retrievable via list_campaign_history."""

    def setUp(self):
        self.session = FakeSession()
        self.repo = CampaignDraftRepository(self.session)
        self.campaign_id = uuid.uuid4()
        self.campaign = _editable_campaign(campaign_id=self.campaign_id)

    def test_edit_draft_replaces_content_plan_with_reviewer_selection(self):
        original_link = SimpleNamespace(content_item_id=CONTENT_ITEM_1)
        with patch.object(CampaignDraftRepository, "get_campaign", return_value=self.campaign), patch.object(
            CampaignDraftRepository, "_get_content_item_links", return_value=[original_link]
        ):
            self.repo.edit_draft(
                DEMO_TENANT_ID,
                self.campaign_id,
                uuid.uuid4(),
                content_items=[
                    {"content_item_id": CONTENT_ITEM_2, "position": 1, "role": "primary"},
                ],
            )

        new_links = [obj for obj in self.session.added if type(obj).__name__ == "CampaignContentItem"]
        self.assertEqual(len(new_links), 1)
        self.assertEqual(new_links[0].content_item_id, CONTENT_ITEM_2)

        audit_rows = [obj for obj in self.session.added if type(obj).__name__ == "SysAuditLog"]
        self.assertEqual(audit_rows[0].before_data["content_item_ids"], [str(CONTENT_ITEM_1)])
        self.assertEqual(audit_rows[0].after_data["content_item_ids"], [str(CONTENT_ITEM_2)])


class OptimisticConcurrencyTests(unittest.TestCase):
    """spec Edge Cases: two reviewers acting on the same campaign at the same
    time must not silently overwrite each other."""

    def setUp(self):
        self.session = FakeSession()
        self.repo = CampaignDraftRepository(self.session)
        self.campaign = _editable_campaign(updated_at=datetime(2026, 1, 1))

    def _conflicting_session(self):
        """A concurrent writer already changed approval_status/updated_at."""

        class _ConflictResult:
            def scalar_one_or_none(self_inner):
                return uuid.uuid4()

            def first(self_inner):
                return SimpleNamespace(updated_at=datetime(2026, 1, 2), approval_status=APPROVAL_STATUS_APPROVED)

        session = self.session
        session.execute = lambda _stmt: _ConflictResult()
        return session

    def test_edit_rejects_actor_missing_from_active_tenant(self):
        self.session.audit_user_exists = False

        with self.assertRaises(CampaignDraftActorNotFoundError):
            self.repo.edit_draft(
                DEMO_TENANT_ID,
                self.campaign.campaign_id,
                uuid.uuid4(),
                objective="Updated objective",
            )

        self.assertFalse(self.session.committed)

    def test_approve_raises_conflict_when_row_changed_concurrently(self):
        self._conflicting_session()
        with patch.object(CampaignDraftRepository, "get_campaign", return_value=self.campaign), patch.object(
            CampaignDraftRepository, "_get_content_item_links", return_value=[]
        ):
            with self.assertRaises(CampaignDraftConflictError):
                self.repo.approve(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())

    def test_reject_raises_conflict_when_row_changed_concurrently(self):
        self._conflicting_session()
        with patch.object(CampaignDraftRepository, "get_campaign", return_value=self.campaign):
            with self.assertRaises(CampaignDraftConflictError):
                self.repo.reject(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4())

    def test_edit_draft_raises_conflict_when_row_changed_concurrently(self):
        self.campaign.objective = "Original"
        self.campaign.strategy_summary = None
        self.campaign.start_date = None
        self.campaign.end_date = None
        self._conflicting_session()
        with patch.object(CampaignDraftRepository, "get_campaign", return_value=self.campaign), patch.object(
            CampaignDraftRepository, "_get_content_item_links", return_value=[]
        ):
            with self.assertRaises(CampaignDraftConflictError):
                self.repo.edit_draft(DEMO_TENANT_ID, self.campaign.campaign_id, uuid.uuid4(), objective="New objective")


class ListCampaignHistoryTests(unittest.TestCase):
    """Regression test: sys_audit_log.created_at is a naive TIMESTAMP while
    crm_campaign_reviews.created_at is TIMESTAMPTZ -- merging/sorting them
    must not crash comparing offset-naive vs. offset-aware datetimes."""

    def test_merges_and_sorts_naive_audit_rows_with_aware_review_rows(self):
        campaign_id = uuid.uuid4()
        audit_row = SimpleNamespace(
            action="CREATE",
            user_id=uuid.uuid4(),
            before_data=None,
            after_data={"objective": "Win back"},
            created_at=datetime(2026, 1, 1, 10, 0, 0),  # naive, like sys_audit_log
        )
        review_row = SimpleNamespace(
            decision="approve",
            reviewer_id=uuid.uuid4(),
            reason=None,
            created_at=datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc),  # aware, like crm_campaign_reviews
        )

        call_results = [_FakeScalarsResult([audit_row]), _FakeScalarsResult([review_row])]

        class _SequencedSession:
            def execute(self, _stmt):
                return call_results.pop(0)

        repo = CampaignDraftRepository(_SequencedSession())

        history = repo.list_campaign_history(DEMO_TENANT_ID, campaign_id)

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["type"], "audit")
        self.assertEqual(history[1]["type"], "review")


class AgentAwareCreateDraftTests(unittest.TestCase):
    """Spec 03: the registry planner is validated before any write, its model/
    hyperparameters/prompt go to the agent, and its snapshot is persisted."""

    def setUp(self):
        self.session = FakeSession(candidate_content_items=[_fake_content_item(CONTENT_ITEM_1, segment_tags=["vip_lapsed"])])
        self.repo = CampaignDraftRepository(self.session)
        for patcher in (
            patch("core.repositories.campaign_draft_repository.SegmentRepository.get_segment", return_value=_fake_segment()),
            patch.object(CampaignDraftRepository, "_get_template", return_value=_fake_template()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _create(self, **overrides):
        kwargs = dict(
            tenant_id=DEMO_TENANT_ID,
            created_by=None,
            segment_id=DEMO_SEGMENT_ID,
            template_id=DEMO_TEMPLATE_ID,
            objective="Win back lapsed customers",
            agent_code=AGENT_CODE,
        )
        kwargs.update(overrides)
        return self.repo.create_draft(**kwargs)

    def test_planner_gets_closed_context_and_campaign_stores_provenance(self):
        with _patch_planner() as resolve, patch(
            "core.repositories.campaign_draft_repository.generate_campaign_plan", return_value=_valid_generated_plan()
        ) as generate:
            campaign = self._create(budget_time_constraints="Two weeks, 5M VND")

        _session, agent_code, context = resolve.call_args.args
        self.assertEqual(agent_code, AGENT_CODE)
        self.assertEqual(context["candidate_content_item_ids"], [str(CONTENT_ITEM_1)])
        self.assertEqual(context["target_segment"]["segment_name"], "Lapsed VIPs")
        self.assertEqual(context["budget"], "Two weeks, 5M VND")
        brief = generate.call_args.args[0]
        self.assertEqual((brief.model, brief.instructions), ("openrouter/openai/gpt-5.6-luna", "Registry prompt v2"))
        self.assertEqual(brief.extra_config, {"temperature": 0.2})
        self.assertEqual(campaign.metadata_, {"agent_provenance": FAKE_PLANNER.snapshot})
        audit = next(obj for obj in self.session.added if type(obj).__name__ == "SysAuditLog")
        self.assertEqual(audit.after_data["instruction_version"], 2)

    def test_marketer_values_win_over_the_plan(self):
        start, end = date.today() + timedelta(days=30), date.today() + timedelta(days=40)
        with _patch_planner() as resolve, patch(
            "core.repositories.campaign_draft_repository.generate_campaign_plan", return_value=_valid_generated_plan()
        ) as generate:
            campaign = self._create(
                name="Tet win-back", campaign_code="TET-01", start_date=start, end_date=end,
                budget_amount=Decimal("5000000"), currency="VND",
            )

        self.assertEqual((campaign.name, campaign.campaign_code), ("Tet win-back", "TET-01"))
        self.assertEqual((campaign.start_date, campaign.end_date), (start, end))
        self.assertEqual(campaign.budget_amount, Decimal("5000000"))
        self.assertEqual(campaign.ai_plan["name"], "Q4 Win-Back")  # the plan itself is kept as proposed
        self.assertEqual(resolve.call_args.args[2]["budget"], "5000000 VND")
        self.assertIn("Budget fixed by the marketer: 5000000 VND", generate.call_args.args[0].budget_time_constraints)

    def test_unusable_agent_blocks_before_any_write_or_ai_call(self):
        with _patch_planner(side_effect=AgentConfigurationInvalidError(AGENT_CODE, ["status is 'INACTIVE'"])), patch(
            "core.repositories.campaign_draft_repository.generate_campaign_plan"
        ) as generate:
            with self.assertRaises(AgentConfigurationInvalidError):
                self._create()

        generate.assert_not_called()
        self.assertEqual(self.session.added, [])
        self.assertFalse(self.session.committed)


class GovernedEditorTests(unittest.TestCase):
    """Spec 03: updated_at precondition, general vs governed fields, template
    revalidation, metadata namespacing and re-planning."""

    LOADED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def setUp(self):
        self.session = FakeSession()
        self.repo = CampaignDraftRepository(self.session)
        self.campaign = _editable_campaign(
            approval_status=APPROVAL_STATUS_APPROVED,
            template_id=DEMO_TEMPLATE_ID,
            updated_at=self.LOADED_AT,
            metadata_={"agent_provenance": {"agent_code": AGENT_CODE, "instruction_version": 1}},
        )
        for patcher in (
            patch.object(CampaignDraftRepository, "get_campaign", return_value=self.campaign),
            patch.object(CampaignDraftRepository, "_get_content_item_links", return_value=[]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _edit(self, **fields):
        return self.repo.edit_draft(DEMO_TENANT_ID, self.campaign.campaign_id, None, expected_updated_at=self.LOADED_AT, **fields)

    def _audit(self):
        return next(obj for obj in self.session.added if type(obj).__name__ == "SysAuditLog")

    def test_stale_updated_at_refuses_without_writing(self):
        with self.assertRaises(CampaignDraftStaleError):
            self.repo.edit_draft(
                DEMO_TENANT_ID, self.campaign.campaign_id, None,
                objective="Late edit", expected_updated_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
            )

        self.assertEqual(self.session.added, [])
        self.assertTrue(self.session.rolled_back)
        self.assertEqual(self.campaign.objective, "Original objective")

    def test_general_field_edit_is_audited_but_keeps_approval(self):
        self._edit(general_fields={"name": "Renamed", "budget_amount": Decimal("99.50")})

        self.assertEqual(self.campaign.approval_status, APPROVAL_STATUS_APPROVED)
        audit = self._audit()
        self.assertEqual((audit.before_data["name"], audit.after_data["name"]), ("Campaign", "Renamed"))
        self.assertEqual(audit.after_data["budget_amount"], "99.50")

    def test_template_change_is_governed_and_revalidated(self):
        new_template = uuid.uuid4()
        with patch.object(CampaignDraftRepository, "_get_template", return_value=SimpleNamespace(status="Approved", metadata_={})):
            self._edit(template_id=new_template)

        self.assertEqual(self.campaign.template_id, new_template)
        self.assertEqual(self.campaign.approval_status, APPROVAL_STATUS_IN_REVIEW)
        self.assertEqual(self._audit().after_data["template_id"], str(new_template))

    def test_unapproved_or_wrong_channel_template_is_refused(self):
        for template in (
            SimpleNamespace(status="Draft", metadata_={}),
            SimpleNamespace(status="Approved", metadata_={"channel": "zalo_zns"}),
            None,
        ):
            with self.subTest(template=template), patch.object(CampaignDraftRepository, "_get_template", return_value=template):
                with self.assertRaises(CampaignDraftValidationError):
                    self._edit(template_id=uuid.uuid4())
        self.assertEqual(self.session.added, [])

    def test_editor_context_merges_without_touching_provenance(self):
        self._edit(editor_context={"notes": "check tone"})

        self.assertEqual(self.campaign.metadata_["editor_context"], {"notes": "check tone"})
        self.assertEqual(self.campaign.metadata_["agent_provenance"]["agent_code"], AGENT_CODE)
        self.assertEqual(self.campaign.approval_status, APPROVAL_STATUS_APPROVED)

    def test_replan_rewrites_plan_and_provenance_but_keeps_marketer_dates(self):
        self.campaign.segment_id = DEMO_SEGMENT_ID
        loaded_dates = (self.campaign.start_date, self.campaign.end_date)
        segment = _fake_segment()
        segment.tenant_id = DEMO_TENANT_ID
        segment.member_count = 10
        self.session._candidate_content_items = [_fake_content_item(CONTENT_ITEM_2, segment_tags=["vip_lapsed"])]
        with patch("core.repositories.campaign_draft_repository.SegmentRepository.get_segment", return_value=segment), _patch_planner() as resolve, patch(
            "core.repositories.campaign_draft_repository.generate_campaign_plan",
            return_value=_valid_generated_plan(content_item_ids=[str(CONTENT_ITEM_2)]),
        ):
            self._edit(replan=True)

        self.assertEqual(resolve.call_args.args[1], AGENT_CODE)  # reuses the campaign's planner
        self.assertEqual(self.campaign.ai_plan["content_item_ids"], [str(CONTENT_ITEM_2)])
        self.assertEqual(self.campaign.metadata_["agent_provenance"], FAKE_PLANNER.snapshot)
        self.assertEqual((self.campaign.start_date, self.campaign.end_date), loaded_dates)
        links = [obj for obj in self.session.added if type(obj).__name__ == "CampaignContentItem"]
        self.assertEqual([link.content_item_id for link in links], [CONTENT_ITEM_2])
        self.assertEqual(self.campaign.approval_status, APPROVAL_STATUS_IN_REVIEW)

    def test_replan_of_zns_campaign_is_refused(self):
        self.campaign.channel = "zalo_zns"

        with self.assertRaises(CampaignDraftValidationError):
            self._edit(replan=True)


if __name__ == "__main__":
    unittest.main()
