"""
Common system metadata and health endpoints.

The router stays transport-focused; metadata/database logic lives in
core.repositories.metadata_repository.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.repositories.metadata_repository import (
    DEFAULT_TENANT_ID,
    MetadataRepository,
    MetadataRepositoryError,
)

# Common metadata router for root-level /metadata endpoints
metadata_router = APIRouter(prefix="/metadata", tags=["System Metadata"])

def get_metadata_repository(db: Session = Depends(get_db)) -> MetadataRepository:
    """Dependency injection for the MetadataRepository."""
    return MetadataRepository(db)


@metadata_router.get("/")
def get_system_metadata(
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, Any]:
    """
    Returns API version, runtime environment, and the status of every
    external service dependency. Useful for dashboards, ops health checks,
    and confirming which optional backends (Redis cache, Dagster pipelines)
    are currently online.
    """
    return repository.get_system_metadata()


@metadata_router.get("/dagster")
def get_dagster_metadata(
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, Any]:
    """
    Returns Dagster webserver connectivity plus the configured
    customer360-backend code locations/jobs that this API can trigger. Does not
    submit or query any job runs.
    """
    return repository.get_dagster_metadata()


@metadata_router.get("/smtp")
def get_smtp_health(
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, Any]:
    """
    SMTP dispatch health check. Reports 'disabled' when email runs in mock
    mode; otherwise actively connects (STARTTLS + login + NOOP) to the configured
    SMTP relay to confirm it is reachable and the credential authenticates.

    Reflects the SYSTEM/env SMTP config (the email_engine send-time fallback),
    not a tenant's per-tenant crm_connector_config EMAIL row. Authenticated like
    /metadata/dagster (only bare GET /metadata is login-screen exempt), and kept
    out of GET /metadata so a real SMTP login never runs on page load.
    """
    return repository.get_smtp_health()


@metadata_router.get("/domains")
def get_metadata_domains(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    repository: MetadataRepository = Depends(get_metadata_repository),
) -> dict[str, str]:
    """
    Returns the business-domain vocabulary enabled for a tenant.

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

all_metadata_routers = [metadata_router]