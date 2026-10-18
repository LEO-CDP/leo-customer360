"""Persistence for customer engagement flows in ``customer360.crm_flow*`` (see database-schema.sql).

The studio's JSON is saved as-is into ``crm_flow_version`` and shredded into node and edge rows
by ``customer360.crm_save_flow``. ``customer360.crm_load_flow`` rebuilds the same JSON, so a saved
flow loads back into the studio unchanged. Every call runs with the tenant's RLS context set.
The caller owns the transaction: commit after ``save`` and ``publish``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import FlowDefinition

Actor = Literal["user", "agent"]


class FlowNotFound(LookupError):
    """No flow, or no such version, for this tenant."""


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
    ) -> SavedFlow:
        """Save a new version of a flow. ``flow_id=None`` creates the flow.

        The JSON is validated first. The stored JSON is the caller's dict, so loading it
        returns the same structure the UI sent.
        """
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Flow name is required")
        if actor not in ("user", "agent"):
            raise ValueError(f"Unknown actor {actor!r}")
        payload = _as_payload(workflow)
        self._set_tenant_context()
        row = self.session.execute(
            text(
                "SELECT flow_id, version FROM customer360.crm_save_flow("
                "CAST(:tenant AS uuid), CAST(:flow AS uuid), :name, CAST(:workflow AS jsonb), :actor)"
            ),
            {
                "tenant": str(self.tenant_id),
                "flow": str(flow_id) if flow_id else None,
                "name": clean_name,
                "workflow": json.dumps(payload),
                "actor": actor,
            },
        ).one()
        return SavedFlow(flow_id=UUID(str(row.flow_id)), version=row.version)

    def publish(self, flow_id: UUID, version: int) -> tuple[int | None, int]:
        """Make ``version`` the live version. Returns (previous live version, new live version)."""
        self._set_tenant_context()
        row = self.session.execute(
            text(
                "SELECT previous_version, live_version FROM customer360.crm_publish_flow("
                "CAST(:tenant AS uuid), CAST(:flow AS uuid), :version)"
            ),
            {"tenant": str(self.tenant_id), "flow": str(flow_id), "version": version},
        ).first()
        if row is None:
            raise FlowNotFound(f"Version {version} of flow {flow_id} does not exist for this tenant")
        return row.previous_version, row.live_version

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
