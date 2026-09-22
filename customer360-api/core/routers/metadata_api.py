
"""System metadata / health endpoints.

The router stays transport-focused; metadata/database logic lives in
core.repositories.metadata_repository.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from core.database import get_db
from leo_customer360_dao.models.identity import CdpAiAgent
from leo_customer360_dao.models.system import SysDataSource
from core.repositories.metadata_repository import (
    DEFAULT_TENANT_ID,
    MetadataConflictError,
    MetadataNotFoundError,
    MetadataRepository,
    MetadataRepositoryError,
)
from leo_customer360_dao.schemas.system import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    AiAgentCreate,
    AiAgentRead,
    AiAgentUpdate,
)

metadata_router = APIRouter(prefix="/metadata", tags=["System Metadata"])

def get_metadata_repository(db: Session = Depends(get_db)) -> MetadataRepository:
    return MetadataRepository(db)


@metadata_router.get("/")
def get_system_metadata(
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, Any]:
    """Returns API version, runtime environment, and the status of every
    external service dependency. Useful for dashboards, ops health checks,
    and confirming which optional backends (Redis cache, Dagster pipelines)
    are currently online."""
    return repository.get_system_metadata()


@metadata_router.get("/dagster")
def get_dagster_metadata(
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, Any]:
    """Returns Dagster webserver connectivity plus the configured
    customer360-backend code locations/jobs that this API can trigger. Does not
    submit or query any job runs."""
    return repository.get_dagster_metadata()


@metadata_router.get("/smtp")
def get_smtp_health(
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, Any]:
    """SMTP dispatch health check. Reports 'disabled' when email runs in mock
    mode; otherwise actively connects (STARTTLS + login + NOOP) to the configured
    SMTP relay to confirm it is reachable and the credential authenticates.

    Reflects the SYSTEM/env SMTP config (the email_engine send-time fallback),
    not a tenant's per-tenant crm_connector_config EMAIL row. Authenticated like
    /metadata/dagster (only bare GET /metadata is login-screen exempt), and kept
    out of GET /metadata so a real SMTP login never runs on page load."""
    return repository.get_smtp_health()


@metadata_router.get("/domains")
def get_metadata_domains(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, str]:
    """Returns the business-domain vocabulary enabled for a tenant.

    Joins ``sys_tenant_domain`` (which domains this tenant has enabled) to
    ``sys_domain`` (the domain code/label catalog), equivalent to:

        SELECT * FROM customer360.sys_tenant_domain WHERE tenant_id = ?

    filtered to active rows on both sides, and returned as a simple
    ``{domain_code: domain_name}`` map so the customer360-frontend UI can render
    domain labels without hard-coding them.
    """
    try:
        return repository.get_domains(tenant_id)
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@metadata_router.get("/data-sources", response_model=list[DataSourceRead])
def list_metadata_data_sources(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    status: int | None = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> list[SysDataSource]:
    """Returns tenant-scoped rows from ``sys_data_source`` for connector setup UIs."""
    try:
        return repository.list_data_sources(
            tenant_id=tenant_id,
            status=status,
            skip=skip,
            limit=limit,
        )
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@metadata_router.get("/data-sources/{data_source_id}", response_model=DataSourceRead)
def get_metadata_data_source(
    data_source_id: uuid.UUID,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> SysDataSource:
    try:
        return repository.get_data_source(data_source_id)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.post("/data-sources", response_model=DataSourceRead, status_code=201)
def create_metadata_data_source(
    payload: DataSourceCreate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> SysDataSource:
    return repository.create_data_source(payload.model_dump())


@metadata_router.patch("/data-sources/{data_source_id}", response_model=DataSourceRead)
def update_metadata_data_source(
    data_source_id: uuid.UUID,
    payload: DataSourceUpdate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> SysDataSource:
    try:
        return repository.update_data_source(data_source_id, payload.model_dump(exclude_unset=True))
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.delete("/data-sources/{data_source_id}", status_code=204)
def delete_metadata_data_source(
    data_source_id: uuid.UUID,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> None:
    try:
        repository.delete_data_source(data_source_id)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.get("/ai-agents", response_model=list[AiAgentRead])
@metadata_router.get("/scoring-models", response_model=list[AiAgentRead], include_in_schema=False)
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


@metadata_router.get("/ai-agents/{agent_code}", response_model=AiAgentRead)
@metadata_router.get("/scoring-models/{agent_code}", response_model=AiAgentRead, include_in_schema=False)
def get_metadata_ai_agent(
    agent_code: str,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpAiAgent:
    try:
        return repository.get_ai_agent(agent_code)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.post("/ai-agents", response_model=AiAgentRead, status_code=201)
@metadata_router.post("/scoring-models", response_model=AiAgentRead, status_code=201, include_in_schema=False)
def create_metadata_ai_agent(
    payload: AiAgentCreate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpAiAgent:
    try:
        return repository.create_ai_agent(payload.model_dump())
    except MetadataConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@metadata_router.patch("/ai-agents/{agent_code}", response_model=AiAgentRead)
@metadata_router.patch("/scoring-models/{agent_code}", response_model=AiAgentRead, include_in_schema=False)
def update_metadata_ai_agent(
    agent_code: str,
    payload: AiAgentUpdate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpAiAgent:
    try:
        return repository.update_ai_agent(agent_code, payload.model_dump(exclude_unset=True))
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.delete("/ai-agents/{agent_code}", status_code=204)
@metadata_router.delete("/scoring-models/{agent_code}", status_code=204, include_in_schema=False)
def delete_metadata_ai_agent(
    agent_code: str,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> None:
    try:
        repository.delete_ai_agent(agent_code)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


all_metadata_routers = [metadata_router]
