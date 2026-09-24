"""
Data source endpoints.
Manages the system data sources required for connector setups.

Follows the verified core/routers/segment_api.py CRUD pattern with Redis
response caching, tenant isolation, and dual route matching (with and without
trailing slashes) for robust reverse proxy (Caddy/Nginx) operation.
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.cache import cache_response, invalidate_prefix
from leo_customer360_dao.config import settings
from core.database import get_db
from leo_customer360_dao.models.system import SysDataSource
from core.repositories.metadata_repository import (
    DEFAULT_TENANT_ID,
    MetadataNotFoundError,
    MetadataRepositoryError,
)
from core.repositories.datasource_repository import DataSourceRepository
from leo_customer360_dao.schemas.system import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
)

logger = logging.getLogger(__name__)

CACHE_PREFIX = "sys_data_source"

data_source_router = APIRouter(prefix="/data-sources", tags=["C360 Data Sources"])


def get_data_source_repository(db: Session = Depends(get_db)) -> DataSourceRepository:
    """Provide the data-source repository for route handlers."""
    return DataSourceRepository(db)


def _get_data_source_or_404(repository: DataSourceRepository, data_source_id: uuid.UUID) -> SysDataSource:
    """Helper following segment_api._get_segment_or_404 pattern."""
    try:
        return repository.get_data_source(data_source_id)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@data_source_router.get("", response_model=list[DataSourceRead], include_in_schema=False)
@data_source_router.get("/", response_model=list[DataSourceRead])
@cache_response(f"{CACHE_PREFIX}/list", ttl=settings.cache_ttl_seconds)
def list_metadata_data_sources(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    status: int | None = None,
    skip: int = 0,
    limit: int = Query(default=settings.api_default_page_size, le=settings.api_max_page_size),
    repository: DataSourceRepository = Depends(get_data_source_repository),
) -> list[SysDataSource]:
    """Returns tenant-scoped rows from ``sys_data_source`` for connector setup UIs.

    Supports both ``/data-sources`` and ``/data-sources/`` to prevent 307
    redirects behind reverse proxies.
    """
    try:
        return repository.list_data_sources(
            tenant_id=tenant_id,
            status=status,
            skip=skip,
            limit=limit,
        )
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@data_source_router.get("/count")
@cache_response(f"{CACHE_PREFIX}/count", ttl=settings.cache_ttl_seconds)
def count_metadata_data_sources(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    status: int | None = None,
    repository: DataSourceRepository = Depends(get_data_source_repository),
) -> dict[str, int]:
    """Returns total count of data sources matching filter criteria."""
    try:
        count = repository.count_data_sources(tenant_id=tenant_id, status=status)
        return {"count": count}
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@data_source_router.get("/{data_source_id}", response_model=DataSourceRead)
@cache_response(f"{CACHE_PREFIX}/item", ttl=settings.cache_ttl_seconds)
def get_metadata_data_source(
    data_source_id: uuid.UUID,
    repository: DataSourceRepository = Depends(get_data_source_repository),
) -> SysDataSource:
    """Retrieves a specific data source by its UUID."""
    return _get_data_source_or_404(repository, data_source_id)


@data_source_router.post("", response_model=DataSourceRead, status_code=201, include_in_schema=False)
@data_source_router.post("/", response_model=DataSourceRead, status_code=201)
def create_metadata_data_source(
    payload: DataSourceCreate,
    repository: DataSourceRepository = Depends(get_data_source_repository),
) -> SysDataSource:
    """Creates a new data source and invalidates the cache."""
    try:
        obj = repository.create_data_source(payload.model_dump())
        invalidate_prefix(CACHE_PREFIX)
        return obj
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@data_source_router.patch("/{data_source_id}", response_model=DataSourceRead)
def update_metadata_data_source(
    data_source_id: uuid.UUID,
    payload: DataSourceUpdate,
    repository: DataSourceRepository = Depends(get_data_source_repository),
) -> SysDataSource:
    """Partially updates an existing data source and invalidates the cache."""
    try:
        obj = repository.update_data_source(data_source_id, payload.model_dump(exclude_unset=True))
        invalidate_prefix(CACHE_PREFIX)
        return obj
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@data_source_router.delete("/{data_source_id}", status_code=204)
def delete_metadata_data_source(
    data_source_id: uuid.UUID,
    repository: DataSourceRepository = Depends(get_data_source_repository),
) -> None:
    """Deletes a specific data source and invalidates the cache."""
    try:
        repository.delete_data_source(data_source_id)
        invalidate_prefix(CACHE_PREFIX)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except MetadataRepositoryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


all_data_source_routers = [data_source_router]
