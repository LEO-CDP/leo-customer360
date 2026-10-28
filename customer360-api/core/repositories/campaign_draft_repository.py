"""AI Campaign Strategy and Draft Creation repository: persistence + state-
machine transitions (Draft/InReview/Approved/Rejected) for crm_campaign,
crm_campaign_content_items, crm_campaign_reviews, reusing the existing
sys_audit_log table for full edit/decision history.

Uses the same synchronous SQLAlchemy Session as the rest of the API (see
core/database.py); tenant isolation is enforced primarily by Postgres RLS
(core.database.get_db already sets app.tenant_id on the session), with an
explicit tenant_id filter on every query here as defense-in-depth (same
pattern as core/repositories/campaign_repository.py).
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from leo_customer360_agent.client import (
    AIProviderError,
    CampaignPlanBrief,
    GeneratedCampaignPlan,
    generate_campaign_plan,
)
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.models.crm import Campaign, CampaignContentItem, CampaignReview
from leo_customer360_dao.models.system import SysAuditLog, SysUser
from leo_customer360_dao.repositories.segment_respository import SegmentRepository
from leo_customer360_dao.schemas.journey import infer_journey_stage_from_objective

APPROVAL_STATUS_DRAFT = "Draft"
APPROVAL_STATUS_IN_REVIEW = "InReview"
APPROVAL_STATUS_APPROVED = "Approved"
APPROVAL_STATUS_REJECTED = "Rejected"
SEGMENT_UNSET = object()
FIELD_UNSET = object()


def _utc_now_naive() -> datetime:
    """sys_audit_log.created_at is `timestamp without time zone` (DB-clock-
    dependent server_default now()), unlike crm_campaign_reviews.created_at
    (`timestamptz`). list_campaign_history() re-labels the naive value as UTC
    to sort the two streams together -- which is only correct if the value
    really is a UTC wall-clock reading. Passing this explicitly at insert
    time (instead of relying on the server default) guarantees that,
    independent of the DB session's timezone GUC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CampaignDraftValidationError(ValueError):
    """Raised for a refused planning request."""


class CampaignDraftNotFoundError(LookupError):
    """Raised when a campaign_id doesn't resolve within the caller's tenant."""


class CampaignSegmentNotFoundError(LookupError):
    """Raised (404) when the referenced segment does not exist (or is
    invisible to the caller's tenant via RLS)."""


class CampaignDraftApprovalBlockedError(RuntimeError):
    """Raised when approve() or reject() is refused: the campaign isn't
    InReview (only InReview campaigns may be approved/rejected), the linked
    template is no longer Approved, or a linked content item is no longer
    active -- FR-013/FR-016."""


class CampaignDraftConflictError(RuntimeError):
    """Raised when a concurrent edit/approve/reject conflicts with the
    campaign's current state (optimistic-concurrency guard)."""


class CampaignDraftActorNotFoundError(LookupError):
    """Raised when the authenticated actor is not a user in the active tenant."""


class CampaignDraftRepository:
    """Persist campaign drafts and enforce their approval state machine."""

    def __init__(self, session: Session):
        """Bind the repository to a tenant-aware SQLAlchemy session."""
        self.session = session

    def get_candidate_content_items(
        self,
        tenant_id: uuid.UUID,
        segment_id: uuid.UUID,
        segment_tag: Optional[str] = None,
        objective: Optional[str] = None,
    ) -> list[CdpContentItem]:
        """FR-006: the closed candidate list the AI is given to choose from
        (never asked to invent items). Tenant + active-only, further
        narrowed by tag/keyword overlap between cdp_content_items.segment_tags
        and the segment's own segment_tag / the marketer's stated objective
        when either is supplied (US3); returns an empty list -- not
        fabricated items -- when nothing overlaps."""
        conditions = [CdpContentItem.tenant_id == tenant_id, CdpContentItem.status_code == 1]

        objective_keywords = {word.lower() for word in (objective or "").split() if len(word) > 3}

        if segment_tag or objective_keywords:
            # SQL-side pre-filter so a large content table doesn't have to be
            # pulled into Python wholesale as the inventory grows. This is a
            # permissive superset of the _matches() check below (array-as-
            # text ILIKE instead of exact case-insensitive tag equality) --
            # _matches() remains the authoritative filter, applied after, so
            # this can only narrow the DB round-trip, never change the result.
            keywords = objective_keywords or set()
            or_clauses = []
            if segment_tag:
                or_clauses.append(func.array_to_string(CdpContentItem.segment_tags, ",").ilike(f"%{segment_tag}%"))
            for keyword in keywords:
                or_clauses.append(CdpContentItem.title.ilike(f"%{keyword}%"))
                or_clauses.append(func.coalesce(CdpContentItem.summary, "").ilike(f"%{keyword}%"))
                or_clauses.append(func.array_to_string(CdpContentItem.segment_tags, ",").ilike(f"%{keyword}%"))
            if or_clauses:
                conditions.append(or_(*or_clauses))

        stmt = select(CdpContentItem).where(*conditions)
        items = list(self.session.execute(stmt).scalars().all())

        if not segment_tag and not objective:
            return items

        def _matches(item: CdpContentItem) -> bool:
            """Apply the authoritative tag and objective match in Python."""
            tags = {tag.lower() for tag in (item.segment_tags or [])}
            if segment_tag and segment_tag.lower() in tags:
                return True
            if objective_keywords and tags & objective_keywords:
                return True
            text = f"{item.title} {item.summary or ''}".lower()
            return bool(objective_keywords) and any(keyword in text for keyword in objective_keywords)

        return [item for item in items if _matches(item)]

    def create_draft(
        self,
        tenant_id: uuid.UUID,
        created_by: Optional[uuid.UUID],
        segment_id: uuid.UUID,
        objective: str,
        budget_time_constraints: Optional[str] = None,
        journey_stage: Optional[str] = None,
    ) -> Campaign:
        """Resolve the segment, build the closed candidate content list, call
        the planning agent, validate the schedule, and persist the campaign
        workflow draft plus content plan.

        Reads and the write are deliberately two separate transactions on
        this session, not one: the AI call in between is an HTTP request
        that can take up to ~30s, and holding a DB transaction (and its
        pooled connection) open for that whole span starves the pool under
        concurrent requests and risks Postgres killing the transaction
        mid-write on a slow response. The intermediate commit() ends the
        read-only transaction with nothing pending; core/database.py's
        after_begin listener reapplies the RLS tenant/user GUCs when the
        write below implicitly starts a new one."""
        segment_repo = SegmentRepository(self.session)
        segment = segment_repo.get_segment(segment_id)
        if segment is None:
            raise CampaignSegmentNotFoundError(f"Segment '{segment_id}' not found")
        if not segment.is_active or segment.status_code != 1:
            raise CampaignDraftValidationError(
                f"Segment '{segment_id}' is not resolvable (inactive or no computed snapshot)"
            )

        candidate_items = self.get_candidate_content_items(
            tenant_id, segment_id, segment_tag=segment.segment_tag, objective=objective
        )
        candidate_payload = [
            {"content_item_id": str(item.content_item_id), "title": item.title, "item_type": item.item_type}
            for item in candidate_items
        ]
        # Extract everything still needed after the AI call as plain values
        # now, while the ORM objects are still attached -- nothing below
        # this point touches segment/candidate_items again.
        segment_name = segment.segment_name
        valid_content_ids = {str(item.content_item_id) for item in candidate_items}

        # End the read-only transaction (nothing pending) before the AI call.
        self.session.commit()

        brief = CampaignPlanBrief(
            segment_context={"segment_id": str(segment_id), "segment_name": segment_name},
            objective=objective,
            budget_time_constraints=budget_time_constraints,
        )
        try:
            generated = generate_campaign_plan(brief, candidate_payload)
        except AIProviderError as exc:
            raise CampaignDraftValidationError(str(exc)) from exc

        if generated.start_date is None or generated.end_date is None or generated.end_date < generated.start_date:
            raise CampaignDraftValidationError("AI-generated schedule window is invalid (end_date before start_date)")
        if generated.end_date < date.today():
            raise CampaignDraftValidationError("AI-generated schedule window is entirely in the past")

        # Defense-in-depth (FR-006): discard any AI-returned id not in the
        # candidate list, even though the prompt already instructs against it.
        selected_ids = [cid for cid in generated.content_item_ids if cid in valid_content_ids]

        resolved_journey_stage = journey_stage or infer_journey_stage_from_objective(objective)

        # New transaction for the write.
        campaign = Campaign(
            tenant_id=tenant_id,
            user_id=created_by,
            name=generated.name,
            status=APPROVAL_STATUS_DRAFT,
            objective=objective,
            journey_stage=resolved_journey_stage,
            segment_id=segment_id,
            approval_status=APPROVAL_STATUS_IN_REVIEW,
            strategy_summary=generated.strategy_summary,
            planning_constraints=budget_time_constraints,
            ai_plan={
                "name": generated.name,
                "objective": generated.objective,
                "journey_stage": resolved_journey_stage,
                "strategy_summary": generated.strategy_summary,
                "planning_constraints": budget_time_constraints,
                "action_plan": generated.action_plan,
                "start_date": generated.start_date.isoformat(),
                "end_date": generated.end_date.isoformat(),
                "content_item_ids": generated.content_item_ids,
            },
            start_date=generated.start_date,
            end_date=generated.end_date,
        )
        self.session.add(campaign)
        self.session.flush()  # assigns campaign.campaign_id

        for position, content_item_id in enumerate(selected_ids, start=1):
            self.session.add(
                CampaignContentItem(
                    tenant_id=tenant_id,
                    campaign_id=campaign.campaign_id,
                    content_item_id=uuid.UUID(content_item_id),
                    position=position,
                    role="primary" if position == 1 else "supporting",
                )
            )

        self.session.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=created_by,
                action="CREATE",
                resource_type="crm_campaign",
                resource_id=str(campaign.campaign_id),
                created_at=_utc_now_naive(),
                after_data={
                    "name": generated.name,
                    "objective": objective,
                    "journey_stage": resolved_journey_stage,
                    "strategy_summary": generated.strategy_summary,
                    "start_date": generated.start_date.isoformat(),
                    "end_date": generated.end_date.isoformat(),
                    "content_item_ids": selected_ids,
                },
            )
        )
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def get_campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
        """Load a tenant-owned campaign or raise the repository not-found error."""
        campaign = self.session.execute(
            select(Campaign).where(Campaign.campaign_id == campaign_id, Campaign.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if campaign is None:
            raise CampaignDraftNotFoundError(f"Campaign '{campaign_id}' not found")
        return campaign

    def list_campaign_content_items(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
        """Return the ordered content plan with display fields for the API."""
        stmt = (
            select(CampaignContentItem, CdpContentItem)
            .join(CdpContentItem, CampaignContentItem.content_item_id == CdpContentItem.content_item_id)
            .where(CampaignContentItem.tenant_id == tenant_id, CampaignContentItem.campaign_id == campaign_id)
            .order_by(CampaignContentItem.position.asc())
        )
        rows = self.session.execute(stmt).all()
        return [
            {
                "content_item_id": link.content_item_id,
                "position": link.position,
                "role": link.role,
                "title": content_item.title,
                "item_type": content_item.item_type,
                "cta_url": content_item.cta_url,
            }
            for link, content_item in rows
        ]

    def _check_not_concurrently_modified(self, campaign: Campaign, expected_updated_at: Any) -> None:
        """Optimistic-concurrency guard (spec Edge Cases: "two reviewers
        attempt to approve/reject/edit the same campaign draft at the same
        time"): re-reads updated_at/approval_status immediately before
        committing and refuses if another transaction changed the row since
        this request first read it."""
        current = self.session.execute(
            select(Campaign.updated_at, Campaign.approval_status)
            .where(Campaign.campaign_id == campaign.campaign_id, Campaign.tenant_id == campaign.tenant_id)
            .with_for_update()
        ).first()
        if current is not None and current.updated_at != expected_updated_at:
            raise CampaignDraftConflictError(
                f"Campaign '{campaign.campaign_id}' was modified by another request "
                f"(current approval_status: {current.approval_status}); reload and retry"
            )

    def _get_content_item_links(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[CampaignContentItem]:
        """Load content links used by edits and approval validation."""
        stmt = select(CampaignContentItem).where(
            CampaignContentItem.tenant_id == tenant_id, CampaignContentItem.campaign_id == campaign_id
        )
        return list(self.session.execute(stmt).scalars().all())

    def edit_draft(
        self,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        editor_id: Optional[uuid.UUID],
        segment_id: object = SEGMENT_UNSET,
        objective: Any = FIELD_UNSET,
        journey_stage: Any = FIELD_UNSET,
        strategy_summary: Any = FIELD_UNSET,
        planning_constraints: Any = FIELD_UNSET,
        start_date: Any = FIELD_UNSET,
        end_date: Any = FIELD_UNSET,
        content_items: Optional[list[dict[str, Any]]] = None,
        workflow_id: Any = FIELD_UNSET,
        workflow_version: Any = FIELD_UNSET,
        commit: bool = True,
    ) -> Campaign:
        """FR-007, FR-008, FR-012, FR-014: validates any supplied schedule
        window, updates the supplied fields, replaces the content plan when
        content_items is supplied, records a sys_audit_log before/after
        snapshot, and returns the campaign to InReview whenever it was
        Approved (fresh approval required) or Rejected (resubmission,
        non-terminal per data-model.md). Set ``commit=False`` when a caller
        must include these edits in a larger atomic transaction."""
        if editor_id is not None:
            editor_exists = self.session.execute(
                select(SysUser.user_id).where(
                    SysUser.tenant_id == tenant_id,
                    SysUser.user_id == editor_id,
                )
            ).scalar_one_or_none()
            if editor_exists is None:
                raise CampaignDraftActorNotFoundError(
                    "Authenticated user is not a member of the active tenant; sign in again"
                )

        campaign = self.get_campaign(tenant_id, campaign_id)
        expected_updated_at = campaign.updated_at
        current_segment_id = campaign.segment_id

        segment_changed = segment_id is not SEGMENT_UNSET and segment_id != current_segment_id
        selected_segment = None
        if segment_changed:
            if not isinstance(segment_id, uuid.UUID):
                raise CampaignDraftValidationError("A campaign must keep a target segment")
            selected_segment = SegmentRepository(self.session).get_segment(segment_id)
            if selected_segment is None:
                raise CampaignDraftValidationError("The selected segment does not belong to the active tenant")
            if selected_segment.tenant_id != tenant_id:
                raise CampaignDraftValidationError("The selected segment does not belong to the active tenant")
            if not selected_segment.is_active or selected_segment.status_code != 1:
                raise CampaignDraftValidationError("The selected segment is inactive or has no computed membership snapshot")

        effective_start = start_date if start_date is not FIELD_UNSET else campaign.start_date
        effective_end = end_date if end_date is not FIELD_UNSET else campaign.end_date
        if effective_start and effective_end and effective_end < effective_start:
            raise CampaignDraftValidationError("end_date cannot be before start_date")

        existing_links = self._get_content_item_links(tenant_id, campaign_id)
        before_data = {
            "segment": self._segment_audit_snapshot(tenant_id, current_segment_id),
            "objective": campaign.objective,
            "journey_stage": getattr(campaign, "journey_stage", None),
            "strategy_summary": campaign.strategy_summary,
            "planning_constraints": getattr(campaign, "planning_constraints", None),
            "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
            "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
            "workflow_id": str(getattr(campaign, "workflow_id", None)) if getattr(campaign, "workflow_id", None) else None,
            "workflow_version": getattr(campaign, "workflow_version", None),
            "content_item_ids": [str(link.content_item_id) for link in existing_links],
        }

        if segment_changed:
            if selected_segment is None:
                raise CampaignDraftValidationError("The selected segment could not be loaded")
            campaign.segment_id = selected_segment.segment_id
            # A strategy generated for the old audience must not be presented
            # as valid for the newly selected audience.
            campaign.strategy_summary = None
            campaign.ai_plan = None

        if objective is not FIELD_UNSET:
            campaign.objective = objective
            if journey_stage is FIELD_UNSET and not getattr(campaign, "journey_stage", None):
                campaign.journey_stage = infer_journey_stage_from_objective(objective)
        if journey_stage is not FIELD_UNSET:
            campaign.journey_stage = journey_stage
        if strategy_summary is not FIELD_UNSET:
            campaign.strategy_summary = strategy_summary
        if planning_constraints is not FIELD_UNSET:
            campaign.planning_constraints = planning_constraints
        if start_date is not FIELD_UNSET:
            campaign.start_date = start_date
        if end_date is not FIELD_UNSET:
            campaign.end_date = end_date
        if workflow_id is not FIELD_UNSET:
            campaign.workflow_id = workflow_id
        if workflow_version is not FIELD_UNSET:
            campaign.workflow_version = workflow_version

        after_content_item_ids = before_data["content_item_ids"]
        if content_items is not None:
            for link in existing_links:
                self.session.delete(link)
            self.session.flush()
            after_content_item_ids = []
            for position, item in enumerate(content_items, start=1):
                content_item_id = item["content_item_id"]
                self.session.add(
                    CampaignContentItem(
                        tenant_id=tenant_id,
                        campaign_id=campaign_id,
                        content_item_id=content_item_id,
                        position=item["position"] if item.get("position") is not None else position,
                        role=item.get("role"),
                    )
                )
                after_content_item_ids.append(str(content_item_id))

        after_data = {
            "segment": self._segment_audit_snapshot(tenant_id, campaign.segment_id),
            "objective": campaign.objective,
            "journey_stage": getattr(campaign, "journey_stage", None),
            "strategy_summary": campaign.strategy_summary,
            "planning_constraints": getattr(campaign, "planning_constraints", None),
            "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
            "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
            "workflow_id": str(getattr(campaign, "workflow_id", None)) if getattr(campaign, "workflow_id", None) else None,
            "workflow_version": getattr(campaign, "workflow_version", None),
            "content_item_ids": after_content_item_ids,
        }
        # Only demote a previously Approved/Rejected campaign back to
        # InReview (forcing re-review) when the edit actually changed
        # something -- a no-op PATCH must not silently revoke approval.
        if after_data != before_data and campaign.approval_status in (
            APPROVAL_STATUS_APPROVED,
            APPROVAL_STATUS_REJECTED,
        ):
            campaign.approval_status = APPROVAL_STATUS_IN_REVIEW

        self._check_not_concurrently_modified(campaign, expected_updated_at)
        campaign.updated_at = datetime.now(timezone.utc)

        self.session.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=editor_id,
                action="UPDATE",
                resource_type="crm_campaign",
                resource_id=str(campaign_id),
                created_at=_utc_now_naive(),
                before_data=before_data,
                after_data=after_data,
            )
        )
        if commit:
            self.session.commit()
            self.session.refresh(campaign)
        return campaign

    def _segment_audit_snapshot(self, tenant_id: uuid.UUID, segment_id: Optional[uuid.UUID]) -> Optional[dict[str, Any]]:
        """Return safe segment identity details for campaign audit history."""
        if segment_id is None:
            return None
        segment = SegmentRepository(self.session).get_segment(segment_id)
        if segment is None or segment.tenant_id != tenant_id:
            return {"segment_id": str(segment_id), "segment_name": None}
        return {
            "segment_id": str(segment.segment_id),
            "segment_name": segment.segment_name,
            "segment_tag": segment.segment_tag,
            "member_count": segment.member_count,
        }

    def submit_for_review(
        self,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        submitter_id: uuid.UUID,
    ) -> Campaign:
        """Move a complete Draft campaign into the admin review queue."""
        campaign = self.get_campaign(tenant_id, campaign_id)
        if campaign.approval_status != APPROVAL_STATUS_DRAFT:
            raise CampaignDraftValidationError(
                f"Campaign '{campaign_id}' is not Draft (current approval_status: "
                f"{campaign.approval_status}); only Draft campaigns can be submitted"
            )
        if campaign.segment_id is None:
            raise CampaignDraftValidationError("A campaign must have a target segment before submission")
        if campaign.workflow_id is None or campaign.workflow_version is None:
            raise CampaignDraftValidationError("A campaign must have a saved workflow before submission")

        expected_updated_at = campaign.updated_at
        self._check_not_concurrently_modified(campaign, expected_updated_at)
        campaign.approval_status = APPROVAL_STATUS_IN_REVIEW
        campaign.updated_at = datetime.now(timezone.utc)
        self.session.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=submitter_id,
                action="SUBMIT_FOR_REVIEW",
                resource_type="crm_campaign",
                resource_id=str(campaign_id),
                created_at=_utc_now_naive(),
                before_data={"approval_status": APPROVAL_STATUS_DRAFT},
                after_data={"approval_status": APPROVAL_STATUS_IN_REVIEW},
            )
        )
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def approve(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, reviewer_id: uuid.UUID) -> Campaign:
        """Re-validates linked content items before recording approval."""
        campaign = self.get_campaign(tenant_id, campaign_id)
        if campaign.approval_status != APPROVAL_STATUS_IN_REVIEW:
            raise CampaignDraftApprovalBlockedError(
                f"Campaign '{campaign_id}' is not InReview (current approval_status: "
                f"{campaign.approval_status}); only InReview campaigns can be approved"
            )
        expected_updated_at = campaign.updated_at

        content_links = self._get_content_item_links(tenant_id, campaign_id)
        if content_links:
            content_item_ids = [link.content_item_id for link in content_links]
            active_ids = {
                item.content_item_id
                for item in self.session.execute(
                    select(CdpContentItem).where(
                        CdpContentItem.content_item_id.in_(content_item_ids),
                        CdpContentItem.tenant_id == tenant_id,
                    )
                )
                .scalars()
                .all()
                if item.status_code == 1
            }
            unavailable = [str(cid) for cid in content_item_ids if cid not in active_ids]
            if unavailable:
                raise CampaignDraftApprovalBlockedError(
                    f"Content item(s) no longer available: {', '.join(unavailable)}"
                )

        self._check_not_concurrently_modified(campaign, expected_updated_at)
        self.session.add(
            CampaignReview(tenant_id=tenant_id, campaign_id=campaign_id, reviewer_id=reviewer_id, decision="approve")
        )
        campaign.approval_status = APPROVAL_STATUS_APPROVED
        campaign.approved_by = reviewer_id
        campaign.approved_at = datetime.now(timezone.utc)
        campaign.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def reject(
        self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, reviewer_id: uuid.UUID, reason: Optional[str] = None
    ) -> Campaign:
        """FR-009, FR-010: records the rejection; Rejected is not terminal
        for campaigns (unlike email templates) -- a subsequent edit resubmits
        to InReview (see edit_draft)."""
        campaign = self.get_campaign(tenant_id, campaign_id)
        if campaign.approval_status != APPROVAL_STATUS_IN_REVIEW:
            raise CampaignDraftApprovalBlockedError(
                f"Campaign '{campaign_id}' is not InReview (current approval_status: "
                f"{campaign.approval_status}); only InReview campaigns can be rejected"
            )
        self._check_not_concurrently_modified(campaign, campaign.updated_at)
        self.session.add(
            CampaignReview(
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                reviewer_id=reviewer_id,
                decision="reject",
                reason=reason,
            )
        )
        campaign.approval_status = APPROVAL_STATUS_REJECTED
        campaign.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        self.session.refresh(campaign)
        return campaign

    def list_campaign_history(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
        """Merge audit and review rows in chronological order.

        The response is returned oldest first so the API can render the complete
        campaign history as a coherent timeline.
        """
        audit_stmt = (
            select(SysAuditLog)
            .where(
                SysAuditLog.tenant_id == tenant_id,
                SysAuditLog.resource_type == "crm_campaign",
                SysAuditLog.resource_id == str(campaign_id),
            )
            .order_by(SysAuditLog.created_at.asc())
        )
        audit_entries = [
            {
                "type": "audit",
                "action": row.action,
                "user_id": row.user_id,
                "before_data": row.before_data,
                "after_data": row.after_data,
                # sys_audit_log.created_at is a naive TIMESTAMP (no time
                # zone); normalize to aware UTC so it sorts correctly
                # alongside crm_campaign_reviews.created_at (TIMESTAMPTZ).
                "created_at": row.created_at.replace(tzinfo=timezone.utc) if row.created_at else None,
            }
            for row in self.session.execute(audit_stmt).scalars().all()
        ]

        review_stmt = (
            select(CampaignReview)
            .where(CampaignReview.tenant_id == tenant_id, CampaignReview.campaign_id == campaign_id)
            .order_by(CampaignReview.created_at.asc())
        )
        review_entries = [
            {
                "type": "review",
                "decision": row.decision,
                "reviewer_id": row.reviewer_id,
                "reason": row.reason,
                "created_at": row.created_at,
            }
            for row in self.session.execute(review_stmt).scalars().all()
        ]

        return sorted(
            audit_entries + review_entries,
            key=lambda entry: entry["created_at"] or datetime.min.replace(tzinfo=timezone.utc),
        )
