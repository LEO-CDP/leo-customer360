"""
AI Agent endpoints.

Manages model and task-agent metadata rows from `cdp_ai_agents`.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from core.database import get_db
from leo_customer360_dao.models.identity import CdpAiAgent
from core.repositories.metadata_repository import (
    MetadataConflictError,
    MetadataNotFoundError,
    MetadataRepository,
    MetadataRepositoryError,
)
from leo_customer360_dao.schemas.system import (
    AiAgentCreate,
    AiAgentRead,
    AiAgentUpdate,
)

# This router owns its public resource prefix independently.
ai_agent_router = APIRouter(prefix="/ai-agents", tags=["C360 - AI Agents"])


def get_metadata_repository(db: Session = Depends(get_db)) -> MetadataRepository:
    """Dependency injection for the MetadataRepository."""
    return MetadataRepository(db)


@ai_agent_router.get("", response_model=list[AiAgentRead])
def list_metadata_ai_agents(
    status: str | None = None,
    model_type: str | None = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> list[CdpAiAgent]:
    """Returns model and task-agent rows from ``cdp_ai_agents``."""
    try:
        return repository.list_ai_agents(
            status=status,
            model_type=model_type,
            skip=skip,
            limit=limit,
        )
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@ai_agent_router.get("/{agent_code}", response_model=AiAgentRead)
def get_metadata_ai_agent(
    agent_code: str,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpAiAgent:
    """Retrieves a specific AI agent by its string agent code."""
    try:
        return repository.get_ai_agent(agent_code)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@ai_agent_router.post("", response_model=AiAgentRead, status_code=201)
def create_metadata_ai_agent(
    payload: AiAgentCreate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpAiAgent:
    """Creates a new AI agent."""
    try:
        return repository.create_ai_agent(payload.model_dump())
    except MetadataConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@ai_agent_router.patch("/{agent_code}", response_model=AiAgentRead)
def update_metadata_ai_agent(
    agent_code: str,
    payload: AiAgentUpdate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpAiAgent:
    """Partially updates an existing AI agent."""
    try:
        return repository.update_ai_agent(agent_code, payload.model_dump(exclude_unset=True))
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@ai_agent_router.delete("/{agent_code}", status_code=204)
def delete_metadata_ai_agent(
    agent_code: str,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> None:
    """Deletes an AI agent by its string agent code."""
    try:
        repository.delete_ai_agent(agent_code)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc