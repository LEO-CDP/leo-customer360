"""
AI Agent endpoints.

Manages model and task-agent metadata rows from `cdp_ai_agents`.
Follows the verified core/routers/segment_api.py CRUD pattern with Redis
response caching and dual route matching (with and without trailing slashes)
for robust reverse proxy (Caddy/Nginx) operation.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from leo_customer360_dao.config import settings
from core.database import get_db
from leo_customer360_dao.models.identity import CdpAiAgent
from core.repositories.metadata_repository import (
    MetadataConflictError,
    MetadataNotFoundError,
    MetadataRepositoryError,
)
from core.repositories.ai_agent_repository import AiAgentRepository
from leo_customer360_dao.schemas.system import (
    AiAgentCreate,
    AiAgentRead,
    AiAgentUpdate,
)

logger = logging.getLogger(__name__)

CACHE_PREFIX = "cdp_ai_agents"

# This router owns its public resource prefix independently.
ai_agent_router = APIRouter(prefix="/ai-agents", tags=["C360 - AI Agents"])


def get_ai_agent_repository(db: Session = Depends(get_db)) -> AiAgentRepository:
    """Provide the AI-agent repository for route handlers."""
    return AiAgentRepository(db)


def _get_ai_agent_or_404(repository: AiAgentRepository, agent_code: str) -> CdpAiAgent:
    """Helper following segment_api._get_segment_or_404 pattern."""
    try:
        return repository.get_ai_agent(agent_code)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@ai_agent_router.get("", response_model=list[AiAgentRead], include_in_schema=False)
@ai_agent_router.get("/", response_model=list[AiAgentRead])
@cache_response(f"{CACHE_PREFIX}/list", ttl=settings.cache_ttl_seconds)
def list_metadata_ai_agents(
    status: str | None = None,
    model_type: str | None = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    repository: AiAgentRepository = Depends(get_ai_agent_repository),
) -> list[CdpAiAgent]:
    """Returns model and task-agent rows from ``cdp_ai_agents``.

    Supports both ``/ai-agents`` and ``/ai-agents/`` to prevent 307
    redirects behind reverse proxies.
    """
    try:
        return repository.list_ai_agents(
            status=status,
            model_type=model_type,
            skip=skip,
            limit=limit,
        )
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@ai_agent_router.get("/count")
@cache_response(f"{CACHE_PREFIX}/count", ttl=settings.cache_ttl_seconds)
def count_metadata_ai_agents(
    status: str | None = None,
    model_type: str | None = None,
    repository: AiAgentRepository = Depends(get_ai_agent_repository),
) -> dict[str, int]:
    """Returns total count of AI agents matching filter criteria."""
    try:
        count = repository.count_ai_agents(status=status, model_type=model_type)
        return {"count": count}
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@ai_agent_router.get("/{agent_code}", response_model=AiAgentRead)
@cache_response(f"{CACHE_PREFIX}/item", ttl=settings.cache_ttl_seconds)
def get_metadata_ai_agent(
    agent_code: str,
    repository: AiAgentRepository = Depends(get_ai_agent_repository),
) -> CdpAiAgent:
    """Retrieves a specific AI agent by its string agent code."""
    return _get_ai_agent_or_404(repository, agent_code)


@ai_agent_router.post("", response_model=AiAgentRead, status_code=201, include_in_schema=False)
@ai_agent_router.post("/", response_model=AiAgentRead, status_code=201)
def create_metadata_ai_agent(
    payload: AiAgentCreate,
    repository: AiAgentRepository = Depends(get_ai_agent_repository),
) -> CdpAiAgent:
    """Creates a new AI agent and invalidates the cache."""
    try:
        obj = repository.create_ai_agent(payload.model_dump())
        invalidate_prefix(CACHE_PREFIX)
        return obj
    except MetadataConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@ai_agent_router.patch("/{agent_code}", response_model=AiAgentRead)
def update_metadata_ai_agent(
    agent_code: str,
    payload: AiAgentUpdate,
    repository: AiAgentRepository = Depends(get_ai_agent_repository),
) -> CdpAiAgent:
    """Partially updates an existing AI agent and invalidates the cache."""
    try:
        obj = repository.update_ai_agent(agent_code, payload.model_dump(exclude_unset=True))
        invalidate_prefix(CACHE_PREFIX)
        return obj
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@ai_agent_router.delete("/{agent_code}", status_code=204)
def delete_metadata_ai_agent(
    agent_code: str,
    repository: AiAgentRepository = Depends(get_ai_agent_repository),
) -> None:
    """Deletes an AI agent by its string agent code and invalidates the cache."""
    try:
        repository.delete_ai_agent(agent_code)
        invalidate_prefix(CACHE_PREFIX)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


all_ai_agent_routers = [ai_agent_router]
