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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
from leo_customer360_dao.models.system import SysAuditLog, SysUser
from leo_customer360_dao.repositories.segment_respository import SegmentRepository

from core.repositories.campaign_planner_agent import resolve_planner

APPROVAL_STATUS_DRAFT = "Draft"
APPROVAL_STATUS_IN_REVIEW = "InReview"
APPROVAL_STATUS_APPROVED = "Approved"
APPROVAL_STATUS_REJECTED = "Rejected"
UNSET = object()
ZNS_CHANNEL = "zalo_zns"


def _marketer_values(campaign_code, budget_amount, currency) -> dict:
    """Unset values are left out so column server defaults such as currency still apply."""
    return {
        key: value
        for key, value in {"campaign_code": campaign_code, "budget_amount": budget_amount, "currency": currency}.items()
        if value is not None
    }

# Editor fields split by ownership: a change to a governed field re-submits an
# Approved/Rejected campaign for review; a general field is audited only.
GOVERNED_AUDIT_KEYS = (
    "segment",
    "template_id",
    "objective",
    "strategy_summary",
    "start_date",
    "end_date",
    "content_item_ids",
    "ai_plan",
)
GENERAL_FIELDS = (
    "campaign_code",
    "name",
    "status",
    "channel",
    "platform",
    "description",
    "keywords",
    "lang",
    "budget_amount",
    "currency",
)


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


class CampaignDraftStaleError(CampaignDraftConflictError):
    """Raised when the editor's ``updated_at`` precondition no longer matches
    the stored campaign: someone saved after the editor loaded it."""


def _jsonable(value: Any) -> Any:
    """Audit snapshots are JSONB: dates/UUIDs/Decimals become strings."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    return value


def _planning_constraints(
    text: Optional[str],
    budget_amount: Optional[Decimal],
    currency: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (budget, time window, constraints text for the planner).
    Marketer-entered budget/dates are stated as fixed constraints; the free
    text stands in for whichever of the two was not entered."""
    budget = f"{budget_amount} {currency or ''}".strip() if budget_amount is not None else text
    window = f"{start_date or 'open'} to {end_date or 'open'}" if (start_date or end_date) else text
    lines = [text] if text else []
    if budget_amount is not None:
        lines.append(f"Budget fixed by the marketer: {budget}")
    if start_date or end_date:
        lines.append(f"Schedule fixed by the marketer: {window}")
    return budget, window, "\n".join(lines) or None


def _email_ai_plan(generated: GeneratedCampaignPlan) -> dict[str, Any]:
    return {
        "name": generated.name,
        "objective": generated.objective,
        "strategy_summary": generated.strategy_summary,
        "action_plan": generated.action_plan,
        "start_date": generated.start_date.isoformat() if generated.start_date else None,
        "end_date": generated.end_date.isoformat() if generated.end_date else None,
        "content_item_ids": generated.content_item_ids,
    }


@dataclass(frozen=True)
class _EmailPlan:
    generated: GeneratedCampaignPlan
    start_date: date
    end_date: date
    selected_ids: list[str]
    provenance: dict[str, Any]


def _validate_planned_window(start_date: Optional[date], end_date: Optional[date]) -> None:
    if start_date is None or end_date is None or end_date < start_date:
        raise CampaignDraftValidationError("AI-generated schedule window is invalid (end_date before start_date)")
    if end_date < date.today():
        raise CampaignDraftValidationError("AI-generated schedule window is entirely in the past")


class CampaignDraftActorNotFoundError(LookupError):
    """Raised when the authenticated actor is not a user in the active tenant."""


class CampaignDraftRepository:
    """Persist campaign drafts and enforce their approval state machine."""

    def __init__(self, session: Session):
        """Bind the repository to a tenant-aware SQLAlchemy session."""
        self.session = session

    def _get_template(self, tenant_id: uuid.UUID, template_id: uuid.UUID) -> Optional[MessageTemplate]:
        """Load one tenant-owned campaign template."""
        return (
            self.session.query(MessageTemplate)
            .filter(MessageTemplate.template_id == template_id, MessageTemplate.tenant_id == tenant_id)
            .one_or_none()
        )

    def list_approved_templates(self, tenant_id: uuid.UUID, channel: str) -> list[dict[str, Any]]:
        """Approved templates of one tenant for a draft form: ``zalo_zns`` or email
        (anything not tagged zalo_zns)."""
        rows = self.session.execute(
            select(MessageTemplate)
            .where(MessageTemplate.tenant_id == tenant_id, MessageTemplate.status == "Approved")
            .order_by(MessageTemplate.name)
        ).scalars().all()
        want_zns = channel == ZNS_CHANNEL
        return [
            {"template_id": t.template_id, "name": t.name, "channel": ZNS_CHANNEL if want_zns else "email"}
            for t in rows
            if ((t.metadata_ or {}).get("channel") == ZNS_CHANNEL) == want_zns
        ]

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

    def _require_resolvable_segment(self, segment_id: uuid.UUID):
        """Load a segment that can be planned against (active, computed)."""
        segment = SegmentRepository(self.session).get_segment(segment_id)
        if segment is None:
            raise CampaignSegmentNotFoundError(f"Segment '{segment_id}' not found")
        if not segment.is_active or segment.status_code != 1:
            raise CampaignDraftValidationError(
                f"Segment '{segment_id}' is not resolvable (inactive or no computed snapshot)"
            )
        return segment

    def _plan_email(
        self,
        tenant_id: uuid.UUID,
        segment,
        objective: str,
        agent_code: str,
        budget_time_constraints: Optional[str],
        budget_amount: Optional[Decimal],
        currency: Optional[str],
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> _EmailPlan:
        """Validate the selected planner against a closed candidate list, then
        call it. Ends the read transaction before the AI call: that HTTP request
        can take up to ~30s, and holding a DB transaction (and its pooled
        connection) open for that span starves the pool under concurrent
        requests. core/database.py's after_begin listener reapplies the RLS
        tenant/user GUCs when the caller's write starts a new transaction."""
        candidate_items = self.get_candidate_content_items(
            tenant_id, segment.segment_id, segment_tag=segment.segment_tag, objective=objective
        )
        candidate_payload = [
            {"content_item_id": str(item.content_item_id), "title": item.title, "item_type": item.item_type}
            for item in candidate_items
        ]
        valid_content_ids = {entry["content_item_id"] for entry in candidate_payload}
        segment_context = {"segment_id": str(segment.segment_id), "segment_name": segment.segment_name}
        budget, window, constraints = _planning_constraints(
            budget_time_constraints, budget_amount, currency, start_date, end_date
        )
        planner = resolve_planner(
            self.session,
            agent_code,
            {
                "target_segment": segment_context,
                "objective": objective,
                "budget": budget,
                "time_constraints": window,
                "candidate_content_item_ids": sorted(valid_content_ids),
                "candidate_content_items": candidate_payload,
            },
        )

        # End the read-only transaction (nothing pending) before the AI call.
        self.session.commit()

        brief = CampaignPlanBrief(
            segment_context=segment_context,
            objective=objective,
            budget_time_constraints=constraints,
            model=planner.model,
            extra_config=planner.extra_config,
            instructions=planner.instructions,
        )
        try:
            generated = generate_campaign_plan(brief, candidate_payload)
        except AIProviderError as exc:
            raise CampaignDraftValidationError(str(exc)) from exc

        effective_start = start_date or generated.start_date
        effective_end = end_date or generated.end_date
        _validate_planned_window(effective_start, effective_end)
        # Defense-in-depth (FR-006): discard any AI-returned id not in the
        # candidate list, even though the prompt already instructs against it.
        selected_ids = [cid for cid in generated.content_item_ids if cid in valid_content_ids]
        return _EmailPlan(generated, effective_start, effective_end, selected_ids, planner.snapshot)

    def _replace_content_links(self, tenant_id: uuid.UUID, campaign_id: uuid.UUID, items: list[dict[str, Any]]) -> list[str]:
        """Insert the ordered content plan; position defaults to list order."""
        for position, item in enumerate(items, start=1):
            self.session.add(
                CampaignContentItem(
                    tenant_id=tenant_id,
                    campaign_id=campaign_id,
                    content_item_id=item["content_item_id"],
                    position=item["position"] if item.get("position") is not None else position,
                    role=item.get("role"),
                )
            )
        return [str(item["content_item_id"]) for item in items]

    def _commit(self) -> None:
        """Commit, mapping the tenant-unique campaign_code constraint to a conflict."""
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise CampaignDraftConflictError(
                "The change conflicts with an existing campaign (campaign_code must be unique within the tenant)"
            ) from exc

    def create_draft(
        self,
        tenant_id: uuid.UUID,
        created_by: Optional[uuid.UUID],
        segment_id: uuid.UUID,
        template_id: uuid.UUID,
        objective: str,
        agent_code: str,
        budget_time_constraints: Optional[str] = None,
        name: Optional[str] = None,
        campaign_code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        budget_amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
    ) -> Campaign:
        """FR-001–FR-006, FR-008: resolve segment + Approved template, validate
        the selected registry planner, build the closed candidate content
        list, call the AI, then persist campaign + content plan + planner
        provenance + audit row. Marketer-entered name/code/dates/budget win
        over the plan. Raises CampaignSegmentNotFoundError/
        CampaignTemplateNotFoundError (404), AgentConfigurationInvalidError or
        CampaignDraftValidationError (409) on any refusal -- nothing is
        persisted on those paths."""
        segment = self._require_resolvable_segment(segment_id)

        template = self._get_template(tenant_id, template_id)
        if template is None:
            raise CampaignTemplateNotFoundError(f"Template '{template_id}' not found")
        if template.status != "Approved":
            raise CampaignDraftValidationError(
                f"Template '{template_id}' is not Approved (current status: {template.status})"
            )

        plan = self._plan_email(
            tenant_id, segment, objective, agent_code, budget_time_constraints,
            budget_amount, currency, start_date, end_date,
        )
        generated = plan.generated

        # New transaction for the write. Unset optional marketer values are
        # left out so the column server defaults (e.g. currency) still apply.
        campaign = Campaign(
            tenant_id=tenant_id,
            user_id=created_by,
            name=name or generated.name,
            status=APPROVAL_STATUS_DRAFT,
            objective=objective,
            segment_id=segment_id,
            template_id=template_id,
            approval_status=APPROVAL_STATUS_IN_REVIEW,
            strategy_summary=generated.strategy_summary,
            ai_plan=_email_ai_plan(generated),
            start_date=plan.start_date,
            end_date=plan.end_date,
            metadata_={"agent_provenance": plan.provenance},
            **_marketer_values(campaign_code, budget_amount, currency),
        )
        self.session.add(campaign)
        self.session.flush()  # assigns campaign.campaign_id

        self._replace_content_links(
            tenant_id,
            campaign.campaign_id,
            [
                {"content_item_id": uuid.UUID(cid), "role": "primary" if position == 1 else "supporting"}
                for position, cid in enumerate(plan.selected_ids, start=1)
            ],
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
                    "name": campaign.name,
                    "objective": objective,
                    "strategy_summary": generated.strategy_summary,
                    "start_date": plan.start_date.isoformat(),
                    "end_date": plan.end_date.isoformat(),
                    "content_item_ids": plan.selected_ids,
                    "agent_code": plan.provenance.get("agent_code"),
                    "instruction_version": plan.provenance.get("instruction_version"),
                },
            )
        )
        self._commit()
        self.session.refresh(campaign)
        return campaign

    def create_zns_draft(
        self,
        tenant_id: uuid.UUID,
        created_by: Optional[uuid.UUID],
        segment_id: uuid.UUID,
        objective: str,
        agent_code: str,
        budget_time_constraints: Optional[str] = None,
        name: Optional[str] = None,
        campaign_code: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        budget_amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
    ) -> Campaign:
        """AI-drafted Zalo ZNS campaign. Unlike ``create_draft``, the caller does
        NOT supply a template: the AI SELECTS one Approved ZNS template
        (crm_message_templates, channel=zalo_zns) from a closed candidate list and
        fills its typed params. Persists a ``channel='zalo_zns'`` crm_campaign
        draft (InReview) with the chosen ``template_id`` + ``ai_plan.template_data``
        (which the notification_engine reads at send time) and the selected
        planner's provenance. Same read-commit-then-AI-then-write shape as
        ``create_draft``."""
        segment = self._require_resolvable_segment(segment_id)

        approved = self.session.execute(
            select(MessageTemplate).where(
                MessageTemplate.tenant_id == tenant_id, MessageTemplate.status == "Approved"
            )
        ).scalars().all()
        candidates = [t for t in approved if (t.metadata_ or {}).get("channel") == ZNS_CHANNEL]
        if not candidates:
            raise CampaignDraftValidationError(
                "No Approved ZNS templates found; sync + approve a ZNS template first"
            )
        candidate_payload = [
            {"template_id": str(t.template_id), "name": t.name, "params": (t.variables or {}).get("params", [])}
            for t in candidates
        ]
        valid_template_ids = {str(t.template_id): t.template_id for t in candidates}
        segment_context = {"segment_id": str(segment_id), "segment_name": segment.segment_name}
        budget, window, constraints = _planning_constraints(
            budget_time_constraints, budget_amount, currency, start_date, end_date
        )
        planner = resolve_planner(
            self.session,
            agent_code,
            {
                "target_segment": segment_context,
                "objective": objective,
                "budget": budget,
                "time_constraints": window,
                "candidate_template_ids": sorted(valid_template_ids),
                "candidate_templates": candidate_payload,
            },
        )

        # End the read-only transaction (nothing pending) before the AI call.
        self.session.commit()

        brief = ZnsCampaignPlanBrief(
            segment_context=segment_context,
            objective=objective,
            budget_time_constraints=constraints,
            model=planner.model,
            extra_config=planner.extra_config,
            instructions=planner.instructions,
        )
        try:
            generated = generate_zalo_campaign_plan(brief, candidate_payload)
        except AIProviderError as exc:
            raise CampaignDraftValidationError(str(exc)) from exc

        effective_start = start_date or generated.start_date
        effective_end = end_date or generated.end_date
        _validate_planned_window(effective_start, effective_end)

        chosen_template_id = valid_template_ids.get(generated.template_id)
        if chosen_template_id is None:  # defense-in-depth (planner already guards this)
            raise CampaignDraftValidationError(
                f"AI selected template '{generated.template_id}' not in the candidate list"
            )

        campaign = Campaign(
            tenant_id=tenant_id,
            user_id=created_by,
            name=name or generated.name,
            status=APPROVAL_STATUS_DRAFT,
            channel=ZNS_CHANNEL,
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
                "start_date": generated.start_date.isoformat() if generated.start_date else None,
                "end_date": generated.end_date.isoformat() if generated.end_date else None,
                "template_id": generated.template_id,
                "template_data": generated.template_data,
            },
            start_date=effective_start,
            end_date=effective_end,
            metadata_={"agent_provenance": planner.snapshot},
            **_marketer_values(campaign_code, budget_amount, currency),
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
                    "channel": ZNS_CHANNEL,
                    "name": campaign.name,
                    "objective": objective,
                    "template_id": str(chosen_template_id),
                    "template_data": generated.template_data,
                    "start_date": effective_start.isoformat(),
                    "end_date": effective_end.isoformat(),
                    "agent_code": planner.snapshot.get("agent_code"),
                    "instruction_version": planner.snapshot.get("instruction_version"),
                },
            )
        )
        self._commit()
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
            self.session.rollback()
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
        segment_id: object = UNSET,
        objective: Optional[str] = None,
        strategy_summary: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        content_items: Optional[list[dict[str, Any]]] = None,
        expected_updated_at: Optional[datetime] = None,
        template_id: object = UNSET,
        general_fields: Optional[dict[str, Any]] = None,
        editor_context: object = UNSET,
        replan: bool = False,
        agent_code: Optional[str] = None,
    ) -> Campaign:
        """FR-007, FR-008, FR-012, FR-014: the governed editor. Checks the
        editor's ``expected_updated_at`` precondition, validates any new
        segment/template/schedule, optionally re-plans with a registry agent,
        applies governed and general fields, records one sys_audit_log
        before/after snapshot, and returns an Approved (fresh approval
        required) or Rejected (resubmission, non-terminal per data-model.md)
        campaign to InReview -- but only when a governed field changed."""
        general_fields = general_fields or {}
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
        if expected_updated_at is not None and campaign.updated_at != expected_updated_at:
            stored = campaign.updated_at
            self.session.rollback()
            raise CampaignDraftStaleError(
                f"Campaign '{campaign_id}' was saved at {stored.isoformat() if stored else 'an unknown time'}, "
                "after this editor loaded it; reload and reapply the changes"
            )
        expected_updated_at = campaign.updated_at
        current_segment_id = campaign.segment_id

        segment_changed = segment_id is not UNSET and segment_id != current_segment_id
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

        effective_channel = general_fields.get("channel", campaign.channel)
        template_changed = template_id is not UNSET and template_id != campaign.template_id
        if template_changed:
            if not isinstance(template_id, uuid.UUID):
                raise CampaignDraftValidationError("A campaign must keep its message template")
            template = self._get_template(tenant_id, template_id)
            if template is None:
                raise CampaignDraftValidationError("The selected template does not belong to the active tenant")
            if template.status != "Approved":
                raise CampaignDraftValidationError(
                    f"The selected template is not Approved (current status: {template.status})"
                )
            is_zns_template = (template.metadata_ or {}).get("channel") == ZNS_CHANNEL
            if is_zns_template != (effective_channel == ZNS_CHANNEL):
                raise CampaignDraftValidationError("The selected template's channel does not match the campaign channel")

        effective_start = start_date if start_date is not None else campaign.start_date
        effective_end = end_date if end_date is not None else campaign.end_date
        if effective_start and effective_end and effective_end < effective_start:
            raise CampaignDraftValidationError("end_date cannot be before start_date")

        existing_links = self._get_content_item_links(tenant_id, campaign_id)
        before_data = self._edit_snapshot(tenant_id, campaign, [str(link.content_item_id) for link in existing_links])

        plan = None
        if replan:
            if effective_channel == ZNS_CHANNEL:
                raise CampaignDraftValidationError(
                    "Re-planning a ZNS campaign is not supported; create a new ZNS draft instead"
                )
            planner_code = agent_code or ((campaign.metadata_ or {}).get("agent_provenance") or {}).get("agent_code")
            if not planner_code:
                raise CampaignDraftValidationError("Re-planning needs an agent_code")
            plan_segment = selected_segment or self._require_resolvable_segment(current_segment_id)
            plan = self._plan_email(
                tenant_id,
                plan_segment,
                objective if objective is not None else campaign.objective,
                planner_code,
                None,
                general_fields.get("budget_amount", campaign.budget_amount),
                general_fields.get("currency", campaign.currency),
                effective_start,
                effective_end,
            )

        if segment_changed:
            campaign.segment_id = selected_segment.segment_id
            # A strategy generated for the old audience must not be presented
            # as valid for the newly selected audience.
            campaign.strategy_summary = None
            campaign.ai_plan = None
        if template_changed:
            campaign.template_id = template_id

        if plan is not None:
            campaign.strategy_summary = plan.generated.strategy_summary
            campaign.ai_plan = _email_ai_plan(plan.generated)
            # Marketer-entered dates stay; the plan only fills empty ones.
            campaign.start_date = effective_start or plan.start_date
            campaign.end_date = effective_end or plan.end_date
            campaign.metadata_ = {**(campaign.metadata_ or {}), "agent_provenance": plan.provenance}
            if content_items is None:
                content_items = [
                    {"content_item_id": uuid.UUID(cid), "role": "primary" if position == 1 else "supporting"}
                    for position, cid in enumerate(plan.selected_ids, start=1)
                ]

        if objective is not None:
            campaign.objective = objective
        if strategy_summary is not None:
            campaign.strategy_summary = strategy_summary
        if start_date is not None:
            campaign.start_date = start_date
        if end_date is not None:
            campaign.end_date = end_date
        for field_name, value in general_fields.items():
            setattr(campaign, field_name, value)
        if editor_context is not UNSET:
            campaign.metadata_ = {**(campaign.metadata_ or {}), "editor_context": editor_context}

        after_content_item_ids = before_data["content_item_ids"]
        if content_items is not None:
            for link in existing_links:
                self.session.delete(link)
            self.session.flush()
            after_content_item_ids = self._replace_content_links(tenant_id, campaign_id, content_items)

        after_data = self._edit_snapshot(tenant_id, campaign, after_content_item_ids)
        # Only a governed change sends a previously Approved/Rejected campaign
        # back to InReview -- a general-field edit or a no-op PATCH must not
        # silently revoke approval.
        governed_changed = any(before_data[key] != after_data[key] for key in GOVERNED_AUDIT_KEYS)
        if governed_changed and campaign.approval_status in (APPROVAL_STATUS_APPROVED, APPROVAL_STATUS_REJECTED):
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
        self._commit()
        self.session.refresh(campaign)
        return campaign

    def _edit_snapshot(self, tenant_id: uuid.UUID, campaign: Campaign, content_item_ids: list[str]) -> dict[str, Any]:
        """Audit snapshot of every editor-owned value (governed + general)."""
        metadata = campaign.metadata_ or {}
        snapshot = {
            "segment": self._segment_audit_snapshot(tenant_id, campaign.segment_id),
            "template_id": _jsonable(campaign.template_id),
            "objective": campaign.objective,
            "strategy_summary": campaign.strategy_summary,
            "start_date": _jsonable(campaign.start_date),
            "end_date": _jsonable(campaign.end_date),
            "content_item_ids": content_item_ids,
            "ai_plan": campaign.ai_plan,
            "agent_instruction_version": (metadata.get("agent_provenance") or {}).get("instruction_version"),
            "editor_context": metadata.get("editor_context"),
        }
        snapshot.update({field: _jsonable(getattr(campaign, field)) for field in GENERAL_FIELDS})
        return snapshot

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
