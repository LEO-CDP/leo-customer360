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

from core.cache import invalidate_prefix
from leo_customer360_agent.client import (
    AIProviderError,
    CampaignPlanBrief,
    GeneratedCampaignPlan,
    ZnsCampaignPlanBrief,
    generate_campaign_plan,
    generate_zalo_campaign_plan,
)
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.models.crm import Campaign, CampaignContentItem, CampaignReview, MessageTemplate
from leo_customer360_dao.models.system import SysAuditLog
from leo_customer360_dao.repositories.segment_respository import SegmentRepository

APPROVAL_STATUS_DRAFT = "Draft"
APPROVAL_STATUS_IN_REVIEW = "InReview"
APPROVAL_STATUS_APPROVED = "Approved"
APPROVAL_STATUS_REJECTED = "Rejected"


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
    """Raised for a refused planning request (unresolvable segment, template
    not Approved, invalid schedule window) -- no crm_campaign row is created."""


class CampaignDraftNotFoundError(LookupError):
    """Raised when a campaign_id doesn't resolve within the caller's tenant."""


class CampaignSegmentNotFoundError(LookupError):
    """Raised (404) when the referenced segment does not exist (or is
    invisible to the caller's tenant via RLS)."""


class CampaignTemplateNotFoundError(LookupError):
    """Raised (404) when the referenced email template does not exist (or is
    invisible to the caller's tenant via RLS)."""


class CampaignDraftApprovalBlockedError(RuntimeError):
    """Raised when approve() or reject() is refused: the campaign isn't
    InReview (only InReview campaigns may be approved/rejected), the linked
    template is no longer Approved, or a linked content item is no longer
    active -- FR-013/FR-016."""


class CampaignDraftConflictError(RuntimeError):
    """Raised when a concurrent edit/approve/reject conflicts with the
    campaign's current state (optimistic-concurrency guard)."""


class CampaignDraftRepository:
    def __init__(self, session: Session):
        self.session = session

    def _get_template(self, tenant_id: uuid.UUID, template_id: uuid.UUID) -> Optional[MessageTemplate]:
        return (
            self.session.query(MessageTemplate)
            .filter(MessageTemplate.template_id == template_id, MessageTemplate.tenant_id == tenant_id)
            .one_or_none()
        )

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
        template_id: uuid.UUID,
        objective: str,
        budget_time_constraints: Optional[str] = None,
    ) -> Campaign:
        """FR-001–FR-006, FR-008: resolve segment + Approved template, build
        the closed candidate content list, call the AI, validate the schedule
        window, then persist campaign + content plan + audit row. Raises
        CampaignSegmentNotFoundError/CampaignTemplateNotFoundError (404) or
        CampaignDraftValidationError (409/422/502-mapped by the router) on
        any refusal -- nothing is persisted on those paths.

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

        template = self._get_template(tenant_id, template_id)
        if template is None:
            raise CampaignTemplateNotFoundError(f"Template '{template_id}' not found")
        if template.status != "Approved":
            raise CampaignDraftValidationError(
                f"Template '{template_id}' is not Approved (current status: {template.status})"
            )

        candidate_items = self.get_candidate_content_items(
            tenant_id, segment_id, segment_tag=getattr(segment, "segment_tag", None), objective=objective
        )
        candidate_payload = [
            {"content_item_id": str(item.content_item_id), "title": item.title, "item_type": item.item_type}
            for item in candidate_items
        ]
        # Extract everything still needed after the AI call as plain values
        # now, while the ORM objects are still attached -- nothing below
        # this point touches segment/template/candidate_items again.
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

        # New transaction for the write.
        campaign = Campaign(
            tenant_id=tenant_id,
            user_id=created_by,
            name=generated.name,
            status=APPROVAL_STATUS_DRAFT,
            objective=objective,
            segment_id=segment_id,
            template_id=template_id,
            approval_status=APPROVAL_STATUS_IN_REVIEW,
            strategy_summary=generated.strategy_summary,
            ai_plan={
                "name": generated.name,
                "objective": generated.objective,
                "strategy_summary": generated.strategy_summary,
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
                    "strategy_summary": generated.strategy_summary,
                    "start_date": generated.start_date.isoformat(),
                    "end_date": generated.end_date.isoformat(),
                    "content_item_ids": selected_ids,
                },
            )
        )
        self.session.commit()
        invalidate_prefix("crm_campaign")
        self.session.refresh(campaign)
        return campaign

    def create_zns_draft(
        self,
        tenant_id: uuid.UUID,
        created_by: Optional[uuid.UUID],
        segment_id: uuid.UUID,
        objective: str,
        budget_time_constraints: Optional[str] = None,
    ) -> Campaign:
        """AI-drafted Zalo ZNS campaign. Unlike ``create_draft``, the caller does
        NOT supply a template: the AI SELECTS one Approved ZNS template
        (crm_message_templates, channel=zalo_zns) from a closed candidate list and
        fills its typed params. Persists a ``channel='zalo_zns'`` crm_campaign
        draft (InReview) with the chosen ``template_id`` + ``ai_plan.template_data``
        (which the notification_engine reads at send time). Same
        read-commit-then-AI-then-write shape as ``create_draft``."""
        segment = SegmentRepository(self.session).get_segment(segment_id)
        if segment is None:
            raise CampaignSegmentNotFoundError(f"Segment '{segment_id}' not found")
        if not segment.is_active or segment.status_code != 1:
            raise CampaignDraftValidationError(
                f"Segment '{segment_id}' is not resolvable (inactive or no computed snapshot)"
            )

        approved = self.session.execute(
            select(MessageTemplate).where(
                MessageTemplate.tenant_id == tenant_id, MessageTemplate.status == "Approved"
            )
        ).scalars().all()
        candidates = [t for t in approved if (t.metadata_ or {}).get("channel") == "zalo_zns"]
        if not candidates:
            raise CampaignDraftValidationError(
                "No Approved ZNS templates found; sync + approve a ZNS template first"
            )
        candidate_payload = [
            {"template_id": str(t.template_id), "name": t.name, "params": (t.variables or {}).get("params", [])}
            for t in candidates
        ]
        valid_template_ids = {str(t.template_id): t.template_id for t in candidates}
        segment_name = segment.segment_name

        # End the read-only transaction (nothing pending) before the AI call.
        self.session.commit()

        brief = ZnsCampaignPlanBrief(
            segment_context={"segment_id": str(segment_id), "segment_name": segment_name},
            objective=objective,
            budget_time_constraints=budget_time_constraints,
        )
        try:
            generated = generate_zalo_campaign_plan(brief, candidate_payload)
        except AIProviderError as exc:
            raise CampaignDraftValidationError(str(exc)) from exc

        if generated.start_date is None or generated.end_date is None or generated.end_date < generated.start_date:
            raise CampaignDraftValidationError("AI-generated schedule window is invalid (end_date before start_date)")
        if generated.end_date < date.today():
            raise CampaignDraftValidationError("AI-generated schedule window is entirely in the past")

        chosen_template_id = valid_template_ids.get(generated.template_id)
        if chosen_template_id is None:  # defense-in-depth (planner already guards this)
            raise CampaignDraftValidationError(
                f"AI selected template '{generated.template_id}' not in the candidate list"
            )

        campaign = Campaign(
            tenant_id=tenant_id,
            user_id=created_by,
            name=generated.name,
            status=APPROVAL_STATUS_DRAFT,
            channel="zalo_zns",
            objective=objective,
            segment_id=segment_id,
            template_id=chosen_template_id,
            approval_status=APPROVAL_STATUS_IN_REVIEW,
            strategy_summary=generated.strategy_summary,
            ai_plan={
                "name": generated.name,
                "objective": generated.objective,
                "strategy_summary": generated.strategy_summary,
                "action_plan": generated.action_plan,
                "start_date": generated.start_date.isoformat(),
                "end_date": generated.end_date.isoformat(),
                "template_id": generated.template_id,
                "template_data": generated.template_data,
            },
            start_date=generated.start_date,
            end_date=generated.end_date,
        )
        self.session.add(campaign)
        self.session.flush()

        self.session.add(
            SysAuditLog(
                tenant_id=tenant_id,
                user_id=created_by,
                action="CREATE",
                resource_type="crm_campaign",
                resource_id=str(campaign.campaign_id),
                created_at=_utc_now_naive(),
                after_data={
                    "channel": "zalo_zns",
                    "name": generated.name,
                    "objective": objective,
                    "template_id": str(chosen_template_id),
                    "template_data": generated.template_data,
                    "start_date": generated.start_date.isoformat(),
                    "end_date": generated.end_date.isoformat(),
                },
            )
        )
        self.session.commit()
        invalidate_prefix("crm_campaign")
        self.session.refresh(campaign)
        return campaign

    def get_campaign(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> Campaign:
        campaign = self.session.execute(
            select(Campaign).where(Campaign.campaign_id == campaign_id, Campaign.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if campaign is None:
            raise CampaignDraftNotFoundError(f"Campaign '{campaign_id}' not found")
        return campaign

    def list_campaign_content_items(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
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
        stmt = select(CampaignContentItem).where(
            CampaignContentItem.tenant_id == tenant_id, CampaignContentItem.campaign_id == campaign_id
        )
        return list(self.session.execute(stmt).scalars().all())

    def edit_draft(
        self,
        tenant_id: uuid.UUID,
        campaign_id: uuid.UUID,
        editor_id: Optional[uuid.UUID],
        objective: Optional[str] = None,
        strategy_summary: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        content_items: Optional[list[dict[str, Any]]] = None,
    ) -> Campaign:
        """FR-007, FR-008, FR-012, FR-014: validates any supplied schedule
        window, updates the supplied fields, replaces the content plan when
        content_items is supplied, records a sys_audit_log before/after
        snapshot, and returns the campaign to InReview whenever it was
        Approved (fresh approval required) or Rejected (resubmission,
        non-terminal per data-model.md)."""
        campaign = self.get_campaign(tenant_id, campaign_id)
        expected_updated_at = campaign.updated_at

        effective_start = start_date if start_date is not None else campaign.start_date
        effective_end = end_date if end_date is not None else campaign.end_date
        if effective_start and effective_end and effective_end < effective_start:
            raise CampaignDraftValidationError("end_date cannot be before start_date")

        existing_links = self._get_content_item_links(tenant_id, campaign_id)
        before_data = {
            "objective": campaign.objective,
            "strategy_summary": campaign.strategy_summary,
            "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
            "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
            "content_item_ids": [str(link.content_item_id) for link in existing_links],
        }

        if objective is not None:
            campaign.objective = objective
        if strategy_summary is not None:
            campaign.strategy_summary = strategy_summary
        if start_date is not None:
            campaign.start_date = start_date
        if end_date is not None:
            campaign.end_date = end_date

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
            "objective": campaign.objective,
            "strategy_summary": campaign.strategy_summary,
            "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
            "end_date": campaign.end_date.isoformat() if campaign.end_date else None,
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
        self.session.commit()
        invalidate_prefix("crm_campaign")
        self.session.refresh(campaign)
        return campaign

    def approve(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, reviewer_id: uuid.UUID) -> Campaign:
        """FR-009, FR-010, FR-013, FR-016: re-validates the linked template is
        still Approved and every linked content item is still active before
        recording the approval; refuses (naming the blocker) otherwise."""
        campaign = self.get_campaign(tenant_id, campaign_id)
        if campaign.approval_status != APPROVAL_STATUS_IN_REVIEW:
            raise CampaignDraftApprovalBlockedError(
                f"Campaign '{campaign_id}' is not InReview (current approval_status: "
                f"{campaign.approval_status}); only InReview campaigns can be approved"
            )
        expected_updated_at = campaign.updated_at

        if campaign.template_id is not None:
            template = self._get_template(tenant_id, campaign.template_id)
            if template is None:
                raise CampaignDraftApprovalBlockedError(
                    f"Linked template '{campaign.template_id}' no longer exists"
                )
            if template.status != "Approved":
                raise CampaignDraftApprovalBlockedError(
                    f"Linked template '{campaign.template_id}' is not Approved (current status: {template.status})"
                )

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
        invalidate_prefix("crm_campaign")
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
        invalidate_prefix("crm_campaign")
        self.session.refresh(campaign)
        return campaign

    def list_campaign_history(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID) -> list[dict[str, Any]]:
        """FR-014, SC-004: merges sys_audit_log create/edit rows with
        crm_campaign_reviews approve/reject rows, oldest first."""
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
