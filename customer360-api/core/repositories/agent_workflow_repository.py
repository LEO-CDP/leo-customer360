"""Persistence and validation for ordered segment AI-agent workflows."""

import logging
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from leo_customer360_dao.models.agent_workflow import CdpAgentWorkflow
from leo_customer360_dao.models.content import CdpContentItem
from leo_customer360_dao.models.identity import CdpAiAgent
from leo_customer360_dao.models.segmentation import CdpSegment
from leo_customer360_dao.schemas.agent_workflow import MAX_CANDIDATE_CONTENT_ITEMS

logger = logging.getLogger(__name__)


class AgentWorkflowNotFoundError(Exception):
    """Raised when a segment or workflow step is not visible to the tenant."""


class AgentWorkflowConflictError(Exception):
    """Raised when a workflow violates an application invariant."""


class AgentWorkflowValidationError(Exception):
    """Raised when referenced workflow data is invalid."""


class AgentWorkflowRepository:
    """Encapsulate tenant-scoped workflow persistence and integrity checks."""

    def __init__(self, session: Session):
        self.session = session

    def _segment(self, tenant_id: uuid.UUID, segment_id: uuid.UUID) -> CdpSegment:
        segment = self.session.scalar(
            select(CdpSegment).where(
                CdpSegment.tenant_id == tenant_id,
                CdpSegment.segment_id == segment_id,
            )
        )
        if segment is None:
            raise AgentWorkflowNotFoundError(f"Segment '{segment_id}' not found")
        return segment

    def _validate_references(
        self,
        tenant_id: uuid.UUID,
        steps: Iterable[dict[str, Any]],
    ) -> None:
        """Validate all cross-table references before changing workflow rows.

        The database triggers remain the final integrity boundary. Performing
        these reads first lets the API return useful messages instead of
        exposing a low-level foreign-key error, while the transaction still
        protects the actual replace operation.
        """
        steps = list(steps)
        if any(
            len(step.get("candidate_content_item_ids") or [])
            > MAX_CANDIDATE_CONTENT_ITEMS
            for step in steps
        ):
            raise AgentWorkflowValidationError(
                "A workflow step may reference at most "
                f"{MAX_CANDIDATE_CONTENT_ITEMS} content items"
            )
        agent_codes = [step["agent_code"] for step in steps]
        if len(agent_codes) != len(set(agent_codes)):
            raise AgentWorkflowConflictError("An agent may appear only once in a segment workflow")

        orders = [step["execution_order"] for step in steps]
        if len(orders) != len(set(orders)):
            raise AgentWorkflowConflictError("Each workflow execution_order must be unique within the segment")

        agent_types: dict[str, str] = {}
        if agent_codes:
            agent_types = {
                agent_code: model_type
                for agent_code, model_type in self.session.execute(
                    select(CdpAiAgent.agent_code, CdpAiAgent.model_type).where(
                        CdpAiAgent.agent_code.in_(agent_codes)
                    )
                ).all()
            }
            existing_agents = set(agent_types)
            missing_agents = sorted(set(agent_codes) - existing_agents)
            if missing_agents:
                raise AgentWorkflowValidationError(
                    f"AI agent(s) not found: {', '.join(missing_agents)}"
                )

        non_ranking_agents = sorted(
            {
                step["agent_code"]
                for step in steps
                if step.get("candidate_content_item_ids")
                and str(agent_types.get(step["agent_code"], "")).upper() != "RANKING_RECOMMENDATION"
            }
        )
        if non_ranking_agents:
            raise AgentWorkflowValidationError(
                "Candidate content items are only supported for "
                "RANKING_RECOMMENDATION agents: "
                + ", ".join(non_ranking_agents)
            )

        ranking_agents_without_candidates = sorted(
            step["agent_code"]
            for step in steps
            if step.get("is_active", True)
            and str(agent_types.get(step["agent_code"], "")).upper()
            == "RANKING_RECOMMENDATION"
            and not step.get("candidate_content_item_ids")
        )
        if ranking_agents_without_candidates:
            raise AgentWorkflowValidationError(
                "Ranking Recommendation agents require at least one selected content item: "
                + ", ".join(ranking_agents_without_candidates)
            )

        candidate_ids = {
            candidate_id
            for step in steps
            for candidate_id in step.get("candidate_content_item_ids", [])
        }
        if not candidate_ids:
            return

        existing_candidates = set(
            self.session.scalars(
                select(CdpContentItem.content_item_id).where(
                    CdpContentItem.tenant_id == tenant_id,
                    CdpContentItem.content_item_id.in_(candidate_ids),
                )
            ).all()
        )
        missing_candidates = sorted(candidate_ids - existing_candidates, key=str)
        if missing_candidates:
            raise AgentWorkflowValidationError(
                "Content item(s) not found in this tenant: "
                + ", ".join(str(item_id) for item_id in missing_candidates)
            )

    def _read_query(self, tenant_id: uuid.UUID, segment_id: uuid.UUID):
        return (
            select(CdpAgentWorkflow, CdpAiAgent)
            .join(CdpAiAgent, CdpAiAgent.agent_code == CdpAgentWorkflow.agent_code)
            .where(
                CdpAgentWorkflow.tenant_id == tenant_id,
                CdpAgentWorkflow.segment_id == segment_id,
            )
            .order_by(CdpAgentWorkflow.execution_order.asc(), CdpAgentWorkflow.agent_code.asc())
        )

    @staticmethod
    def _read_value(step: CdpAgentWorkflow, agent: CdpAiAgent) -> dict[str, Any]:
        return {
            "agent_workflow_id": step.agent_workflow_id,
            "tenant_id": step.tenant_id,
            "segment_id": step.segment_id,
            "agent_code": step.agent_code,
            "execution_order": step.execution_order,
            "is_active": step.is_active,
            "schedule_definition": step.schedule_definition,
            "candidate_content_item_ids": list(step.candidate_content_item_ids or []),
            "configuration": dict(step.configuration or {}),
            "agent_display_name": agent.display_name,
            "agent_model_type": agent.model_type,
            "agent_status": agent.status,
            "agent_schedule_definition": agent.schedule_definition,
            "effective_schedule_definition": (
                step.schedule_definition or agent.schedule_definition
            ),
            "created_at": step.created_at,
            "updated_at": step.updated_at,
        }

    def list_steps(self, tenant_id: uuid.UUID, segment_id: uuid.UUID) -> list[dict[str, Any]]:
        self._segment(tenant_id, segment_id)
        return [
            self._read_value(step, agent)
            for step, agent in self.session.execute(self._read_query(tenant_id, segment_id)).all()
        ]

    def replace_steps(
        self,
        tenant_id: uuid.UUID,
        segment_id: uuid.UUID,
        steps: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self._segment(tenant_id, segment_id)
        self._validate_references(tenant_id, steps)
        try:
            # Reordering can temporarily put two rows at the same position.
            # Deferring this constraint makes the delete-and-insert replace
            # atomic and prevents clients from seeing a partial queue.
            self.session.execute(text("SET CONSTRAINTS customer360.uq_cdp_agent_workflow_execution_order DEFERRED"))
            self.session.execute(
                delete(CdpAgentWorkflow).where(
                    CdpAgentWorkflow.tenant_id == tenant_id,
                    CdpAgentWorkflow.segment_id == segment_id,
                )
            )
            self.session.add_all(
                [
                    CdpAgentWorkflow(
                        tenant_id=tenant_id,
                        segment_id=segment_id,
                        agent_code=step["agent_code"],
                        execution_order=step["execution_order"],
                        is_active=step.get("is_active", True),
                        schedule_definition=step.get("schedule_definition"),
                        candidate_content_item_ids=step.get("candidate_content_item_ids", []),
                        configuration=step.get("configuration", {}),
                    )
                    for step in steps
                ]
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise AgentWorkflowConflictError(self._integrity_detail(exc)) from exc
        except Exception:
            self.session.rollback()
            raise
        return self.list_steps(tenant_id, segment_id)

    def create_step(
        self,
        tenant_id: uuid.UUID,
        segment_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._segment(tenant_id, segment_id)
        self._validate_references(tenant_id, [payload])
        step = CdpAgentWorkflow(tenant_id=tenant_id, segment_id=segment_id, **payload)
        try:
            self.session.add(step)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise AgentWorkflowConflictError(self._integrity_detail(exc)) from exc
        return self.get_step(tenant_id, segment_id, step.agent_workflow_id)

    def get_step(
        self,
        tenant_id: uuid.UUID,
        segment_id: uuid.UUID,
        workflow_id: uuid.UUID,
    ) -> dict[str, Any]:
        self._segment(tenant_id, segment_id)
        result = self.session.execute(
            self._read_query(tenant_id, segment_id).where(
                CdpAgentWorkflow.agent_workflow_id == workflow_id
            )
        ).first()
        if result is None:
            raise AgentWorkflowNotFoundError(f"Workflow step '{workflow_id}' not found")
        return self._read_value(*result)

    def update_step(
        self,
        tenant_id: uuid.UUID,
        segment_id: uuid.UUID,
        workflow_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.get_step(tenant_id, segment_id, workflow_id)
        merged = {
            key: current[key]
            for key in (
                "agent_code",
                "execution_order",
                "is_active",
                "schedule_definition",
                "candidate_content_item_ids",
                "configuration",
            )
        }
        merged.update(payload)
        self._validate_references(tenant_id, [merged])
        obj = self.session.scalar(
            select(CdpAgentWorkflow).where(
                CdpAgentWorkflow.agent_workflow_id == workflow_id,
                CdpAgentWorkflow.tenant_id == tenant_id,
                CdpAgentWorkflow.segment_id == segment_id,
            )
        )
        if obj is None:
            raise AgentWorkflowNotFoundError(f"Workflow step '{workflow_id}' not found")
        for key, value in merged.items():
            setattr(obj, key, value)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise AgentWorkflowConflictError(self._integrity_detail(exc)) from exc
        return self.get_step(tenant_id, segment_id, workflow_id)

    def delete_step(self, tenant_id: uuid.UUID, segment_id: uuid.UUID, workflow_id: uuid.UUID) -> None:
        self.get_step(tenant_id, segment_id, workflow_id)
        deleted = self.session.execute(
            delete(CdpAgentWorkflow).where(
                CdpAgentWorkflow.tenant_id == tenant_id,
                CdpAgentWorkflow.segment_id == segment_id,
                CdpAgentWorkflow.agent_workflow_id == workflow_id,
            )
        ).rowcount
        if not deleted:
            raise AgentWorkflowNotFoundError(f"Workflow step '{workflow_id}' not found")
        self.session.commit()

    @staticmethod
    def _integrity_detail(exc: IntegrityError) -> str:
        name = getattr(getattr(exc, "orig", None), "diag", None)
        constraint = getattr(name, "constraint_name", None)
        if constraint == "uq_cdp_agent_workflow_segment_agent":
            return "This agent is already configured for the segment."
        if constraint == "uq_cdp_agent_workflow_execution_order":
            return "Each workflow execution order must be unique within the segment."
        return "The workflow could not be saved because it conflicts with existing data."
