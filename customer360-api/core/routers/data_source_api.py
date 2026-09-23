"""
Data source  endpoints.
Manages the system data sources required for connector setups.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from leo_customer360_dao.config import settings
from core.database import get_db
from leo_customer360_dao.models.system import SysDataSource
from core.repositories.metadata_repository import (
    DEFAULT_TENANT_ID,
    MetadataNotFoundError,
    MetadataRepository,
    MetadataRepositoryError,
)
from leo_customer360_dao.schemas.system import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
)


data_source_router = APIRouter(prefix="/data-sources", tags=["C360 Data Sources"])


def get_metadata_repository(db: Session = Depends(get_db)) -> MetadataRepository:
    """Dependency injection for the MetadataRepository."""
    return MetadataRepository(db)


@data_source_router.get("/", response_model=list[DataSourceRead])
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


@data_source_router.get("/{data_source_id}", response_model=DataSourceRead)
def get_metadata_data_source(
    data_source_id: uuid.UUID,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> SysDataSource:
    """Retrieves a specific data source by its UUID."""
    try:
        return repository.get_data_source(data_source_id)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@data_source_router.post("/", response_model=DataSourceRead, status_code=201)
def create_metadata_data_source(
    payload: DataSourceCreate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> SysDataSource:
    """Creates a new data source."""
    return repository.create_data_source(payload.model_dump())


@data_source_router.patch("/{data_source_id}", response_model=DataSourceRead)
def update_metadata_data_source(
    data_source_id: uuid.UUID,
    payload: DataSourceUpdate,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> SysDataSource:
    """Partially updates an existing data source."""
    try:
        return repository.update_data_source(data_source_id, payload.model_dump(exclude_unset=True))
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@data_source_router.delete("/{data_source_id}", status_code=204)
def delete_metadata_data_source(
    data_source_id: uuid.UUID,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> None:
    """Deletes a specific data source."""
    try:
        repository.delete_data_source(data_source_id)
    except MetadataNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc