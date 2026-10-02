"""Tenant-scoped API for ordered AI-agent workflows on segments."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.auth import require_tenant
from core.cache import invalidate_prefix
from core.database import get_db
from core.repositories.agent_workflow_repository import (
    AgentWorkflowConflictError,
    AgentWorkflowNotFoundError,
    AgentWorkflowRepository,
    AgentWorkflowValidationError,
)
from leo_customer360_dao.schemas.agent_workflow import (
    AgentWorkflowReplace,
    AgentWorkflowStepCreate,
    AgentWorkflowStepRead,
    AgentWorkflowStepUpdate,
)

router = APIRouter(prefix="/segments", tags=["Segment Agent Workflows"])
TenantId = Annotated[str, Depends(require_tenant)]


def _repository(db: Session) -> AgentWorkflowRepository:
    return AgentWorkflowRepository(db)


def _tenant_uuid(tenant_id: str) -> uuid.UUID:
    return uuid.UUID(tenant_id)


def _handle_error(exc: Exception) -> None:
    if isinstance(exc, AgentWorkflowNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AgentWorkflowValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, AgentWorkflowConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, IntegrityError):
        raise HTTPException(status_code=409, detail="Workflow conflicts with existing data") from exc
    raise exc


@router.get("/{segment_id}/workflow", response_model=list[AgentWorkflowStepRead])
def list_agent_workflow(
    segment_id: uuid.UUID,
    tenant_id: TenantId,
    db: Session = Depends(get_db),
) -> list[dict]:
    """List enabled and disabled workflow steps in execution order."""
    try:
        return _repository(db).list_steps(_tenant_uuid(tenant_id), segment_id)
    except Exception as exc:
        _handle_error(exc)
        raise


@router.put("/{segment_id}/workflow", response_model=list[AgentWorkflowStepRead])
def replace_agent_workflow(
    segment_id: uuid.UUID,
    payload: AgentWorkflowReplace,
    tenant_id: TenantId,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Replace all segment steps atomically, supporting queue reordering."""
    try:
        result = _repository(db).replace_steps(
            _tenant_uuid(tenant_id),
            segment_id,
            [step.model_dump() for step in payload.steps],
        )
        invalidate_prefix("cdp_agent_workflow")
        return result
    except Exception as exc:
        _handle_error(exc)
        raise


@router.post(
    "/{segment_id}/workflow",
    response_model=AgentWorkflowStepRead,
    status_code=status.HTTP_201_CREATED,
)
def create_agent_workflow_step(
    segment_id: uuid.UUID,
    payload: AgentWorkflowStepCreate,
    tenant_id: TenantId,
    db: Session = Depends(get_db),
) -> dict:
    """Add one agent step to a segment workflow."""
    try:
        result = _repository(db).create_step(
            _tenant_uuid(tenant_id),
            segment_id,
            payload.model_dump(),
        )
        invalidate_prefix("cdp_agent_workflow")
        return result
    except Exception as exc:
        _handle_error(exc)
        raise


@router.patch("/{segment_id}/workflow/{workflow_id}", response_model=AgentWorkflowStepRead)
def update_agent_workflow_step(
    segment_id: uuid.UUID,
    workflow_id: uuid.UUID,
    payload: AgentWorkflowStepUpdate,
    tenant_id: TenantId,
    db: Session = Depends(get_db),
) -> dict:
    """Partially update one workflow step."""
    try:
        result = _repository(db).update_step(
            _tenant_uuid(tenant_id),
            segment_id,
            workflow_id,
            payload.model_dump(exclude_unset=True),
        )
        invalidate_prefix("cdp_agent_workflow")
        return result
    except Exception as exc:
        _handle_error(exc)
        raise


@router.delete("/{segment_id}/workflow/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent_workflow_step(
    segment_id: uuid.UUID,
    workflow_id: uuid.UUID,
    tenant_id: TenantId,
    db: Session = Depends(get_db),
) -> None:
    """Remove one agent step from a segment workflow."""
    try:
        _repository(db).delete_step(_tenant_uuid(tenant_id), segment_id, workflow_id)
        invalidate_prefix("cdp_agent_workflow")
    except Exception as exc:
        _handle_error(exc)
