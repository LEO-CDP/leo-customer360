"""Atomic campaign and customer-journey workflow persistence."""

from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.repositories.campaign_draft_repository import (
    FIELD_UNSET,
    SEGMENT_UNSET,
    CampaignDraftActorNotFoundError,
    CampaignDraftConflictError,
    CampaignDraftRepository,
)
from core.repositories.segment_repository import SegmentRepository
from leo_customer360_dao.models.crm import Campaign
from leo_customer360_dao.models.system import SysUser
from leo_customer360_dao.schemas.workflow import CampaignWorkflowSaveRequest
from leo_customer360_dao.workflow import FlowDefinition, FlowEngine, WorkflowRepository


class CampaignWorkflowValidationError(ValueError):
    """The submitted campaign or workflow cannot be saved together."""


class CampaignWorkflowNotFound(LookupError):
    """The campaign or linked workflow does not exist for the active tenant."""


class CampaignWorkflowRepository:
    """Coordinate campaign governance and versioned workflow writes in one transaction."""

    def __init__(self, session: Session, tenant_id: uuid.UUID, user_id: uuid.UUID | None = None):
        self.session = session
        self.tenant_id = tenant_id
        self.user_id = user_id

    @staticmethod
    def _workflow_name(campaign_id: uuid.UUID) -> str:
        return f"Campaign {campaign_id} workflow"

    def _validate_tenant(self, payload: CampaignWorkflowSaveRequest) -> None:
        if payload.campaign.tenant_id != self.tenant_id:
            raise PermissionError("Campaign tenant does not match the authenticated tenant")

    def _validate_segment(self, segment_id: uuid.UUID | None):
        if segment_id is None:
            raise CampaignWorkflowValidationError("Choose an active audience segment before saving.")
        segment = SegmentRepository(self.session).get_segment(segment_id)
        if segment is None or segment.tenant_id != self.tenant_id:
            raise CampaignWorkflowNotFound("The selected audience segment was not found for this tenant.")
        if not segment.is_active or segment.status_code != 1:
            raise CampaignWorkflowValidationError(
                "The selected audience segment is inactive or has no computed membership snapshot."
            )
        return segment

    @staticmethod
    def _validate_campaign_fields(payload: CampaignWorkflowSaveRequest) -> None:
        campaign = payload.campaign
        if not campaign.name.strip():
            raise CampaignWorkflowValidationError("Campaign name is required.")
        if campaign.start_date and campaign.end_date and campaign.end_date < campaign.start_date:
            raise CampaignWorkflowValidationError("End date cannot be before start date.")

    @staticmethod
    def _validate_workflow(workflow: FlowDefinition, segment_id: uuid.UUID) -> None:
        root = workflow.root
        if root.type != "trigger":
            raise CampaignWorkflowValidationError("A customer journey must start with an Event trigger.")
        if root.branch is not None:
            raise CampaignWorkflowValidationError("The Event trigger cannot have a branch.")
        if str(root.config.get("segment_id") or "") != str(segment_id):
            raise CampaignWorkflowValidationError(
                "The workflow audience must match the campaign audience."
            )
        if root.config.get("event") not in {
            "signup",
            "cart_abandoned",
            "purchase",
            "inactive_30d",
            "birthday",
        }:
            raise CampaignWorkflowValidationError("The Event trigger must use a supported customer event.")

        allowed_branches = {
            "condition": {"yes", "no"},
            "wait_event": {"happened", "timeout"},
        }
        for node, parent in workflow.walk():
            if not node.id.strip():
                raise CampaignWorkflowValidationError("Every workflow node must have a non-empty ID.")
            if node.type == "trigger" and parent is not None:
                raise CampaignWorkflowValidationError("An Event trigger can only be the root node.")
            config = node.config
            if node.type == "condition":
                field, operator, value = (config.get(key) for key in ("field", "op", "value"))
                if field not in {
                    "engagement_score",
                    "churn_probability",
                    "lead_conversion_probability",
                    "historical_clv",
                    "predictive_clv",
                    "latest_nps_score",
                    "average_csat",
                    "overall_sentiment_score",
                    "profile_completeness_score",
                    "identity_confidence_score",
                    "segmentation_tags",
                }:
                    raise CampaignWorkflowValidationError(
                        f"Condition node {node.id} must use a supported profile field."
                    )
                valid_operator = (
                    operator in {"has", "hasnt"}
                    if field == "segmentation_tags"
                    else operator in {"ge", "lt", "eq"}
                )
                if not valid_operator or value is None or not str(value).strip():
                    raise CampaignWorkflowValidationError(
                        f"Condition node {node.id} has an invalid operator or value."
                    )
                if field != "segmentation_tags":
                    try:
                        number = float(value)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise CampaignWorkflowValidationError(
                            f"Condition node {node.id} requires a numeric value."
                        ) from exc
                    if not math.isfinite(number):
                        raise CampaignWorkflowValidationError(
                            f"Condition node {node.id} requires a finite numeric value."
                        )
            elif node.type in {"wait", "wait_event"}:
                try:
                    amount = float(config.get("amount"))
                except (TypeError, ValueError, OverflowError) as exc:
                    raise CampaignWorkflowValidationError(
                        f"Wait node {node.id} requires a numeric duration."
                    ) from exc
                if not math.isfinite(amount) or amount < 1:
                    raise CampaignWorkflowValidationError(
                        f"Wait node {node.id} duration must be at least one."
                    )
                if config.get("unit") not in {"minutes", "hours", "days"}:
                    raise CampaignWorkflowValidationError(
                        f"Wait node {node.id} must use minutes, hours, or days."
                    )
                if node.type == "wait_event" and config.get("event") not in {
                    "purchase",
                    "email_opened",
                    "email_clicked",
                }:
                    raise CampaignWorkflowValidationError(
                        f"Wait-for-event node {node.id} must use a supported customer event."
                    )
            elif node.type == "exit" and config.get("kind") not in {"goal", "exit"}:
                raise CampaignWorkflowValidationError(
                    f"Exit node {node.id} must be a goal or an exit."
                )
            elif node.type == "email":
                if any(not isinstance(config.get(key), str) or not config[key].strip() for key in ("subject", "body")):
                    raise CampaignWorkflowValidationError(
                        f"Email node {node.id} requires a subject and body."
                    )
            elif node.type == "notification":
                if not isinstance(config.get("body"), str) or not config["body"].strip():
                    raise CampaignWorkflowValidationError(f"Notification node {node.id} requires a message.")
            elif node.type == "push":
                if any(not isinstance(config.get(key), str) or not config[key].strip() for key in ("title", "body")):
                    raise CampaignWorkflowValidationError(
                        f"Push node {node.id} requires a title and body."
                    )
            elif node.type == "update_profile":
                if config.get("action") not in {"add_tag", "remove_tag"}:
                    raise CampaignWorkflowValidationError(
                        f"Profile update node {node.id} requires a valid tag action."
                    )
                if not isinstance(config.get("tag"), str) or not config["tag"].strip():
                    raise CampaignWorkflowValidationError(
                        f"Profile update node {node.id} requires a tag."
                    )
            if node.type == "exit" and node.children:
                raise CampaignWorkflowValidationError(f"Exit node {node.id} cannot have children.")
            max_children = len(allowed_branches[node.type]) if node.type in allowed_branches else 1
            if len(node.children) > max_children:
                raise CampaignWorkflowValidationError(
                    f"Workflow node {node.id} allows at most {max_children} child node(s)."
                )
            branches = allowed_branches.get(node.type)
            seen_branches: set[str] = set()
            for child in node.children:
                if branches is None:
                    if child.branch is not None:
                        raise CampaignWorkflowValidationError(
                            f"Node {node.id} does not support child branches."
                        )
                    continue
                if child.branch not in branches or child.branch in seen_branches:
                    raise CampaignWorkflowValidationError(
                        f"Children of {node.id} must use unique branches: {', '.join(sorted(branches))}."
                    )
                seen_branches.add(child.branch)

        try:
            engine = FlowEngine(workflow)
        except ValueError as exc:
            raise CampaignWorkflowValidationError(str(exc)) from exc
        problems = engine.problems()
        if problems:
            raise CampaignWorkflowValidationError("Workflow configuration is invalid: " + "; ".join(problems))

    @staticmethod
    def _workflow_payload(workflow: FlowDefinition) -> dict[str, Any]:
        return workflow.model_dump(mode="json", exclude_none=True)

    def _save_workflow(
        self,
        campaign_id: uuid.UUID,
        workflow: FlowDefinition,
        current_flow_id: uuid.UUID | None = None,
        current_version: int | None = None,
    ):
        repository = WorkflowRepository(self.session, self.tenant_id)
        new_payload = self._workflow_payload(workflow)
        if current_flow_id is not None:
            if current_version is None:
                raise CampaignWorkflowValidationError(
                    "The campaign has a workflow ID without a saved workflow version."
                )
            current_payload = repository.load_workflow(current_flow_id, current_version)
            if current_payload == new_payload:
                return current_flow_id, current_version

        saved = repository.save(
            self._workflow_name(campaign_id),
            new_payload,
            flow_id=current_flow_id,
            actor="user",
            user_id=self.user_id,
        )
        return saved.flow_id, saved.version

    def _require_owner(self) -> uuid.UUID:
        """Return the authenticated user who will own a new campaign, verified as a tenant member."""
        if self.user_id is None:
            raise CampaignDraftActorNotFoundError("Authentication required to create a campaign")
        is_member = self.session.execute(
            select(SysUser.user_id).where(
                SysUser.tenant_id == self.tenant_id,
                SysUser.user_id == self.user_id,
            )
        ).scalar_one_or_none()
        if is_member is None:
            raise CampaignDraftActorNotFoundError(
                "Authenticated user is not a member of the active tenant; sign in again"
            )
        return self.user_id

    def create(self, payload: CampaignWorkflowSaveRequest) -> Campaign:
        owner_id = self._require_owner()
        self._validate_tenant(payload)
        self._validate_campaign_fields(payload)
        campaign_values = payload.campaign.model_dump(exclude_unset=True)
        segment_id = payload.campaign.segment_id
        self._validate_segment(segment_id)
        self._validate_workflow(payload.workflow, segment_id)

        from core.repositories.crm_repository import CrmRepository

        CrmRepository(self.session).validate_create_campaign(campaign_values)
        create_values = {
            key: value
            for key, value in campaign_values.items()
            if key
            not in {
                "tenant_id",
                "user_id",
                "approval_status",
                "approved_by",
                "approved_at",
                "ai_plan",
                "metadata_",
            }
        }
        campaign = Campaign(
            **create_values,
            tenant_id=self.tenant_id,
            user_id=owner_id,
            approval_status="Draft",
        )
        self.session.add(campaign)
        try:
            self.session.flush()
            flow_id, version = self._save_workflow(campaign.campaign_id, payload.workflow)
            campaign.workflow_id = flow_id
            campaign.workflow_version = version
            self.session.flush()
            self.session.refresh(campaign)
            self.session.commit()
            return campaign
        except Exception:
            self.session.rollback()
            raise

    def update(self, campaign_id: uuid.UUID, payload: CampaignWorkflowSaveRequest) -> Campaign:
        self._require_owner()
        self._validate_tenant(payload)
        self._validate_campaign_fields(payload)
        from core.repositories.campaign_draft_repository import (
            CampaignDraftNotFoundError,
            CampaignDraftRepository,
        )
        from core.repositories.crm_repository import CrmRepository

        drafts = CampaignDraftRepository(self.session)
        try:
            campaign = drafts.get_campaign(self.tenant_id, campaign_id)
        except CampaignDraftNotFoundError as exc:
            raise CampaignWorkflowNotFound(str(exc)) from exc
        if getattr(campaign, "status", None) == "Running":
            raise CampaignDraftConflictError("A running campaign cannot be edited.")

        values = payload.campaign.model_dump(exclude_unset=True)
        selected_segment_id = (
            payload.campaign.segment_id
            if "segment_id" in payload.campaign.model_fields_set
            else campaign.segment_id
        )
        self._validate_segment(selected_segment_id)
        self._validate_workflow(payload.workflow, selected_segment_id)

        try:
            flow_id, version = self._save_workflow(
                campaign_id,
                payload.workflow,
                current_flow_id=campaign.workflow_id,
                current_version=campaign.workflow_version,
            )

            fields_set = payload.campaign.model_fields_set
            campaign = drafts.edit_draft(
                tenant_id=self.tenant_id,
                campaign_id=campaign_id,
                editor_id=self.user_id,
                segment_id=selected_segment_id if "segment_id" in fields_set else SEGMENT_UNSET,
                objective=payload.campaign.objective if "objective" in fields_set else FIELD_UNSET,
                strategy_summary=(
                    payload.campaign.strategy_summary
                    if "strategy_summary" in fields_set
                    else FIELD_UNSET
                ),
                planning_constraints=(
                    payload.campaign.planning_constraints
                    if "planning_constraints" in fields_set
                    else FIELD_UNSET
                ),
                start_date=payload.campaign.start_date if "start_date" in fields_set else FIELD_UNSET,
                end_date=payload.campaign.end_date if "end_date" in fields_set else FIELD_UNSET,
                workflow_id=flow_id,
                workflow_version=version,
                commit=False,
            )

            general_fields = {
                key: value
                for key, value in values.items()
                if key
                in {
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
                }
            }
            CrmRepository(self.session).validate_campaign_update(campaign, general_fields)
            for key, value in general_fields.items():
                setattr(campaign, key, value)

            self.session.flush()
            self.session.refresh(campaign)
            self.session.commit()
            return campaign
        except Exception:
            self.session.rollback()
            raise

    def load(self, campaign_id: uuid.UUID) -> tuple[Campaign, dict[str, Any] | None, dict[str, Any] | None]:
        """Return the campaign, its pinned workflow JSON, and that version's governance state."""
        from core.repositories.campaign_draft_repository import CampaignDraftNotFoundError

        try:
            campaign = CampaignDraftRepository(self.session).get_campaign(self.tenant_id, campaign_id)
        except CampaignDraftNotFoundError as exc:
            raise CampaignWorkflowNotFound(str(exc)) from exc

        if campaign.workflow_id is None:
            return campaign, None, None
        if campaign.workflow_version is None:
            raise CampaignWorkflowValidationError(
                "The campaign workflow link is missing its saved version."
            )
        repository = WorkflowRepository(self.session, self.tenant_id)
        workflow = repository.load_workflow(campaign.workflow_id, campaign.workflow_version)
        version_info = repository.version_info(campaign.workflow_id, campaign.workflow_version)
        return campaign, workflow, version_info
