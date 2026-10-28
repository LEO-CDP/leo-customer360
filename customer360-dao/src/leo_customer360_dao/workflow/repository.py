"""Persistence for customer engagement flows in ``customer360.crm_flow*`` (see database-schema.sql).

The studio's JSON is saved as-is into ``crm_flow_version`` and shredded into node and edge rows
by ``customer360.crm_save_flow``. ``customer360.crm_load_flow`` rebuilds the same JSON, so a saved
flow loads back into the studio unchanged. Every call runs with the tenant's RLS context set.

Governance: user saves are approved at once. Agent saves are ``InReview`` and cannot be published
until a person other than the run's initiator approves them through ``review``. Rule violations
raised by the database arrive as ``WorkflowRuleViolation``; after any of these errors the
transaction is aborted, so the caller must roll back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .engine import FlowEngine
from .models import FlowDefinition

Actor = Literal["user", "agent"]
ReviewDecision = Literal["approve", "reject"]
REVIEW_DECISIONS = ("approve", "reject")
RAISE_EXCEPTION_SQLSTATE = "P0001"


class FlowNotFound(LookupError):
    """No flow, or no such version, for this tenant."""


class WorkflowRuleViolation(RuntimeError):
    """The database refused the change because the workflow's state does not allow it.

    Examples: publishing an unapproved version, saving into an archived flow, or an initiator
    approving their own agent run. The message is the database's own explanation.
    """


@dataclass(frozen=True)
class SavedFlow:
    flow_id: UUID
    version: int


def read_workflow_file(path: str | Path) -> dict[str, Any]:
    """Load a workflow JSON exported from the studio and validate it."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    FlowDefinition.model_validate(payload)
    return payload


class WorkflowRepository:
    def __init__(self, session: Session, tenant_id: UUID | str) -> None:
        self.session = session
        self.tenant_id = UUID(str(tenant_id))

    def _set_tenant_context(self) -> None:
        self.session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(self.tenant_id)},
        )

    def save(
        self,
        name: str,
        workflow: dict[str, Any] | FlowDefinition,
        flow_id: UUID | None = None,
        actor: Actor = "user",
        *,
        user_id: UUID | None = None,
        agent_code: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> SavedFlow:
        """Save a new version of a flow. ``flow_id=None`` creates the flow.

        ``user_id`` is the person saving (for a user save, also the approver of record). An
        agent save must give ``agent_code`` and may give ``provenance`` (model, prompt version,
        rationale). The JSON is validated first. The stored JSON is the caller's dict, so
        loading it returns the same structure the UI sent.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Flow name is required")
        if actor not in ("user", "agent"):
            raise ValueError(f"Unknown actor {actor!r}")
        if actor == "agent" and not (agent_code and agent_code.strip()):
            raise ValueError("Agent-created workflow versions must name their agent")
        if actor == "agent" and user_id is None:
            raise ValueError("Agent-created workflow versions must name their initiating user")
        if actor == "user" and agent_code is not None:
            raise ValueError("Only agent-created workflow versions can name an agent")
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError("provenance must be a JSON object")
        payload = _as_payload(workflow)
        problems = FlowEngine(FlowDefinition.model_validate(payload)).problems()
        if problems:
            raise ValueError("Workflow has problems: " + "; ".join(problems))
        self._set_tenant_context()
        try:
            row = self.session.execute(
                text(
                    "SELECT flow_id, version FROM customer360.crm_save_flow("
                    "CAST(:tenant AS uuid), CAST(:flow AS uuid), :name, CAST(:workflow AS jsonb), :actor, "
                    "CAST(:user AS uuid), CAST(:agent AS varchar), CAST(:provenance AS jsonb))"
                ),
                {
                    "tenant": str(self.tenant_id),
                    "flow": str(flow_id) if flow_id else None,
                    "name": clean_name,
                    "workflow": json.dumps(payload),
                    "actor": actor,
                    "user": str(user_id) if user_id else None,
                    "agent": agent_code.strip() if agent_code else None,
                    "provenance": json.dumps(provenance or {}),
                },
            ).one()
        except DBAPIError as error:
            mapped = _workflow_error(error)
            if mapped is None:
                raise
            raise mapped from error
        return SavedFlow(flow_id=UUID(str(row.flow_id)), version=row.version)

    def publish(self, flow_id: UUID, version: int) -> tuple[int | None, int]:
        """Make ``version`` the live version. Returns (previous live version, new live version).

        Only an approved version of a non-archived flow can be published.
        """
        self._set_tenant_context()
        try:
            row = self.session.execute(
                text(
                    "SELECT previous_version, live_version FROM customer360.crm_publish_flow("
                    "CAST(:tenant AS uuid), CAST(:flow AS uuid), :version)"
                ),
                {"tenant": str(self.tenant_id), "flow": str(flow_id), "version": version},
            ).first()
        except DBAPIError as error:
            mapped = _workflow_error(error)
            if mapped is None:
                raise
            raise mapped from error
        if row is None:
            raise FlowNotFound(f"Version {version} of flow {flow_id} does not exist for this tenant")
        return row.previous_version, row.live_version

    def review(
        self,
        flow_id: UUID,
        version: int,
        reviewer_id: UUID,
        decision: ReviewDecision,
        reason: str | None = None,
    ) -> str:
        """Approve or reject an InReview version. Returns the resulting approval status.

        The reviewer must be a person, and must not be the person who started the run.
        """
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"decision must be approve or reject, not {decision!r}")
        if reviewer_id is None:
            raise ValueError("A human reviewer is required")
        self._set_tenant_context()
        try:
            row = self.session.execute(
                text(
                    "SELECT reviewed_status FROM customer360.crm_review_flow_version("
                    "CAST(:tenant AS uuid), CAST(:flow AS uuid), :version, CAST(:reviewer AS uuid), "
                    ":decision, :reason)"
                ),
                {
                    "tenant": str(self.tenant_id),
                    "flow": str(flow_id),
                    "version": version,
                    "reviewer": str(reviewer_id),
                    "decision": decision,
                    "reason": reason,
                },
            ).one()
        except DBAPIError as error:
            mapped = _workflow_error(error)
            if mapped is None:
                raise
            raise mapped from error
        return row.reviewed_status

    def version_info(self, flow_id: UUID, version: int) -> dict[str, Any]:
        """Authorship, agent and review state of one version (never the JSON itself)."""
        self._set_tenant_context()
        row = self.session.execute(
            text(
                "SELECT version, created_by, agent_code, approval_status, reviewed_by, reviewed_at, "
                "review_reason, created_at FROM customer360.crm_flow_version "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND flow_id = CAST(:flow AS uuid) "
                "AND version = :version"
            ),
            {"tenant": str(self.tenant_id), "flow": str(flow_id), "version": version},
        ).mappings().first()
        if row is None:
            raise FlowNotFound(f"Version {version} of flow {flow_id} does not exist for this tenant")
        return dict(row)

    def load_workflow(self, flow_id: UUID, version: int | None = None) -> dict[str, Any]:
        """The JSON for the studio's ``setWorkflow``. ``version=None`` loads the live version, else the latest."""
        self._set_tenant_context()
        payload = self.session.execute(
            text(
                "SELECT customer360.crm_load_flow(CAST(:tenant AS uuid), CAST(:flow AS uuid), CAST(:version AS int))"
            ),
            {"tenant": str(self.tenant_id), "flow": str(flow_id), "version": version},
        ).scalar_one_or_none()
        if payload is None:
            raise FlowNotFound(f"Flow {flow_id} version {version or 'live'} not found for this tenant")
        return payload

    def load_definition(self, flow_id: UUID, version: int | None = None) -> FlowDefinition:
        return FlowDefinition.model_validate(self.load_workflow(flow_id, version))

    def list_flows(self) -> list[dict[str, Any]]:
        self._set_tenant_context()
        rows = self.session.execute(
            text(
                "SELECT flow_id, name, status, live_version FROM customer360.crm_flow "
                "WHERE tenant_id = CAST(:tenant AS uuid) ORDER BY created_at DESC"
            ),
            {"tenant": str(self.tenant_id)},
        ).mappings()
        return [dict(row) for row in rows]

    def find_flow_id(self, name: str) -> UUID:
        self._set_tenant_context()
        row = self.session.execute(
            text(
                "SELECT flow_id FROM customer360.crm_flow "
                "WHERE tenant_id = CAST(:tenant AS uuid) AND name = :name"
            ),
            {"tenant": str(self.tenant_id), "name": name},
        ).first()
        if row is None:
            raise FlowNotFound(f'No flow named "{name}" for this tenant')
        return UUID(str(row.flow_id))


def _as_payload(workflow: dict[str, Any] | FlowDefinition) -> dict[str, Any]:
    if isinstance(workflow, FlowDefinition):
        return json.loads(workflow.model_dump_json(exclude_none=True))
    FlowDefinition.model_validate(workflow)
    return workflow


def _workflow_error(error: DBAPIError) -> Exception | None:
    """Translate a database RAISE EXCEPTION (SQLSTATE P0001) into a workflow error.

    Returns None for every other database error, which the caller re-raises unchanged.
    """
    orig = error.orig
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    if code != RAISE_EXCEPTION_SQLSTATE:
        return None
    diag = getattr(orig, "diag", None)
    message = getattr(diag, "message_primary", None) or str(orig).strip().splitlines()[0]
    message = message.strip()
    if "not found" in message:
        return FlowNotFound(message)
    return WorkflowRuleViolation(message)
