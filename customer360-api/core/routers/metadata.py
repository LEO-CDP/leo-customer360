
"""System metadata / health endpoints.

The router stays transport-focused; metadata/database logic lives in
core.repositories.metadata_repository.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_db
from core.models.identity import CdpScoringModel
from core.models.system import SysDataSource
from core.repositories.metadata_repository import (
    DEFAULT_TENANT_ID,
    MetadataConflictError,
    MetadataNotFoundError,
    MetadataRepository,
    MetadataRepositoryError,
)
from core.schemas.system import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    ScoringModelCreate,
    ScoringModelRead,
    ScoringModelUpdate,
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
    backend-system code locations/jobs that this API can trigger. Does not
    submit or query any job runs."""
    return repository.get_dagster_metadata()


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
    ``{domain_code: domain_name}`` map so the frontend-admin UI can render
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


@metadata_router.get("/scoring-models", response_model=list[ScoringModelRead])
def list_metadata_scoring_models(
    status: str | None = None,
    model_type: str | None = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> list[CdpScoringModel]:
    """Returns catalog rows from ``cdp_scoring_models``."""
    try:
        return repository.list_scoring_models(
            status=status,
            model_type=model_type,
            skip=skip,
            limit=limit,
        )
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@metadata_router.get("/scoring-models/{scoring_model_name}", response_model=ScoringModelRead)
def get_metadata_scoring_model(
    scoring_model_name: str,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpScoringModel:
    try:
        return repository.get_scoring_model(scoring_model_name)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.post("/scoring-models", response_model=ScoringModelRead, status_code=201)
def create_metadata_scoring_model(
    payload: ScoringModelCreate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpScoringModel:
    try:
        return repository.create_scoring_model(payload.model_dump())
    except MetadataConflictError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@metadata_router.patch("/scoring-models/{scoring_model_name}", response_model=ScoringModelRead)
def update_metadata_scoring_model(
    scoring_model_name: str,
    payload: ScoringModelUpdate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> CdpScoringModel:
    try:
        return repository.update_scoring_model(scoring_model_name, payload.model_dump(exclude_unset=True))
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@metadata_router.delete("/scoring-models/{scoring_model_name}", status_code=204)
def delete_metadata_scoring_model(
    scoring_model_name: str,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> None:
    try:
        repository.delete_scoring_model(scoring_model_name)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


all_metadata_routers = [metadata_router]
