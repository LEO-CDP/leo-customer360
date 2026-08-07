
"""System metadata / health endpoints.

Returns read-only information about the API version, runtime environment,
and the health of the external services the API depends on: PostgreSQL,
Redis (response cache), and the Dagster webserver that orchestrates the
backend-system pipelines.

These endpoints are intentionally public (see ``core.auth.EXEMPT_PATHS``)
because they expose only configuration/metadata, never credentials or
tenant-scoped data.
"""

import logging
import socket
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.cache import get_redis_client
from core.config import settings
from core.crud.base import CRUDBase
from core.database import engine, get_db
from core.models.identity import CdpScoringModel
from core.models.system import SysDataSource, SysDomain, SysTenantDomain
from core.schemas.system import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    ScoringModelCreate,
    ScoringModelRead,
    ScoringModelUpdate,
)
from core.utils.dagster_client import DagsterClient

logger = logging.getLogger(__name__)

metadata_router = APIRouter(prefix="/metadata", tags=["System Metadata"])

# Short timeouts keep the metadata endpoint responsive even when a service
# is down; a slow dependency should not make this endpoint hang.
_CONNECTIVITY_TIMEOUT_SECONDS = 2

# the default tenant created by database-init/init-core-database.sql
DEFAULT_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_data_source_crud = CRUDBase(SysDataSource)
_scoring_model_crud = CRUDBase(CdpScoringModel)

def _check_postgres() -> dict[str, Any]:
    """Checks that the pooled SQLAlchemy engine can reach PostgreSQL."""
    result = {
        "service": "postgres",
        "status": "unknown",
        "host": settings.db_host,
        "port": settings.db_port,
        "database": settings.db_name,
    }
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        result["status"] = "reachable"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Postgres health check failed", exc_info=True)
        result["status"] = "unreachable"
        result["error"] = str(exc)
    return result


def _check_redis() -> dict[str, Any]:
    """Checks Redis cache connectivity, or reports it as disabled."""
    result = {
        "service": "redis",
        "status": "unknown",
        "host": settings.redis_host,
        "port": settings.redis_port,
        "db": settings.redis_db,
    }
    client = get_redis_client()
    if client is None:
        result["status"] = "disabled"
        result["note"] = "Response caching is disabled or Redis is not configured"
        return result

    try:
        client.ping()
        result["status"] = "reachable"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis health check failed", exc_info=True)
        result["status"] = "unreachable"
        result["error"] = str(exc)
    return result


def _check_dagster() -> dict[str, Any]:
    """Checks whether the Dagster GraphQL webserver is accepting TCP
    connections. A successful connection here means the webserver is up;
    it does not prove every code location is loaded (that's best checked in
    the dedicated ``/metadata/dagster`` endpoint)."""
    result = {
        "service": "dagster",
        "status": "unknown",
        "host": settings.dagster_graphql_host,
        "port": settings.dagster_graphql_port,
    }
    try:
        sock = socket.create_connection(
            (settings.dagster_graphql_host, settings.dagster_graphql_port),
            timeout=_CONNECTIVITY_TIMEOUT_SECONDS,
        )
        sock.close()
        result["status"] = "reachable"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dagster health check failed", exc_info=True)
        result["status"] = "unreachable"
        result["error"] = str(exc)
    return result


def _service_status() -> dict[str, Any]:
    return {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "dagster": _check_dagster(),
    }


@metadata_router.get("/")
def get_system_metadata() -> dict[str, Any]:
    """Returns API version, runtime environment, and the status of every
    external service dependency. Useful for dashboards, ops health checks,
    and confirming which optional backends (Redis cache, Dagster pipelines)
    are currently online."""
    services = _service_status()
    overall = "healthy" if all(s["status"] in (
        "reachable", "disabled") for s in services.values()) else "degraded"
    return {
        "service": "customer360-api",
        "api_version": settings.api_version,
        "environment": settings.environment,
        "sso_login": settings.sso_login,
        "overall_status": overall,
        "services": services,
    }


@metadata_router.get("/dagster")
def get_dagster_metadata() -> dict[str, Any]:
    """Returns Dagster webserver connectivity plus the configured
    backend-system code locations/jobs that this API can trigger. Does not
    submit or query any job runs."""
    connectivity = _check_dagster()
    client = DagsterClient()
    services = []
    for attr_name in dir(client):
        if attr_name.startswith("_"):
            continue
        service = getattr(client, attr_name)
        if not hasattr(service, "job_name"):
            continue
        services.append(
            {
                "name": attr_name,
                "job_name": service.job_name,
                "location_name": service.location_name,
                "repository_name": service.repository_name,
            }
        )
    return {
        "service": "dagster",
        "status": connectivity["status"],
        "host": connectivity.get("host"),
        "port": connectivity.get("port"),
        "error": connectivity.get("error"),
        "configured_services": services,
    }


@metadata_router.get("/domains")
def get_metadata_domains(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Returns the business-domain vocabulary enabled for a tenant.

    Joins ``sys_tenant_domain`` (which domains this tenant has enabled) to
    ``sys_domain`` (the domain code/label catalog), equivalent to:

        SELECT * FROM customer360.sys_tenant_domain WHERE tenant_id = ?

    filtered to active rows on both sides, and returned as a simple
    ``{domain_code: domain_name}`` map so the frontend-admin UI can render
    domain labels without hard-coding them.
    """
    stmt = (
        select(SysDomain.domain_code, SysDomain.domain_name)
        .join(SysTenantDomain, SysTenantDomain.domain_id == SysDomain.domain_id)
        .where(
            SysTenantDomain.tenant_id == tenant_id,
            SysTenantDomain.is_active.is_(True),
            SysDomain.is_active.is_(True),
        )
        .order_by(SysDomain.display_order, SysDomain.domain_code)
    )
    try:
        rows = db.execute(stmt).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load domain metadata from PostgreSQL", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Domain metadata unavailable: {exc}"
        ) from exc
    return {domain_code: domain_name for domain_code, domain_name in rows}


@metadata_router.get("/data-sources", response_model=list[DataSourceRead])
def list_metadata_data_sources(
    tenant_id: uuid.UUID = DEFAULT_TENANT_ID,
    status: int | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[SysDataSource]:
    """Returns tenant-scoped rows from ``sys_data_source`` for connector setup UIs."""
    try:
        return _data_source_crud.list(
            db,
            tenant_id=tenant_id,
            status=status,
            skip=skip,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load data-source metadata from PostgreSQL", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Data-source metadata unavailable: {exc}",
        ) from exc


def _generate_qr_code_data(data_source_url: str, slug: str) -> dict[str, Any]:
    tracking_url = (
        f"{data_source_url}?utm_source={slug}&utm_medium=qr_code&utm_campaign=c360_datasource"
        if "?" not in data_source_url
        else f"{data_source_url}&utm_source={slug}&utm_medium=qr_code&utm_campaign=c360_datasource"
    )
    return {
        "target_url": data_source_url,
        "tracking_url": tracking_url,
        "qr_code_url": f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={quote_plus(tracking_url)}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@metadata_router.get("/data-sources/{data_source_id}", response_model=DataSourceRead)
def get_metadata_data_source(
    data_source_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> SysDataSource:
    obj = _data_source_crud.get(db, data_source_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"SysDataSource '{data_source_id}' not found")
    return obj


@metadata_router.post("/data-sources", response_model=DataSourceRead, status_code=201)
def create_metadata_data_source(
    payload: DataSourceCreate,
    db: Session = Depends(get_db),
) -> SysDataSource:
    data = payload.model_dump()
    if data.get("data_source_url") and not data.get("qr_code_data"):
        data["qr_code_data"] = _generate_qr_code_data(data["data_source_url"], data.get("slug", "datasource"))
    return _data_source_crud.create(db, data)


@metadata_router.patch("/data-sources/{data_source_id}", response_model=DataSourceRead)
def update_metadata_data_source(
    data_source_id: uuid.UUID,
    payload: DataSourceUpdate,
    db: Session = Depends(get_db),
) -> SysDataSource:
    obj = _data_source_crud.get(db, data_source_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"SysDataSource '{data_source_id}' not found")
    data = payload.model_dump(exclude_unset=True)
    if "data_source_url" in data and data["data_source_url"] and "qr_code_data" not in data:
        slug = data.get("slug") or obj.slug or "datasource"
        data["qr_code_data"] = _generate_qr_code_data(data["data_source_url"], slug)
    return _data_source_crud.update(db, obj, data)


@metadata_router.delete("/data-sources/{data_source_id}", status_code=204)
def delete_metadata_data_source(
    data_source_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> None:
    obj = _data_source_crud.get(db, data_source_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"SysDataSource '{data_source_id}' not found")
    _data_source_crud.delete(db, obj)


@metadata_router.get("/scoring-models", response_model=list[ScoringModelRead])
def list_metadata_scoring_models(
    status: str | None = None,
    model_type: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[CdpScoringModel]:
    """Returns catalog rows from ``cdp_scoring_models``."""
    try:
        return _scoring_model_crud.list(
            db,
            status=status,
            model_type=model_type,
            skip=skip,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load scoring model metadata from PostgreSQL", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Scoring model metadata unavailable: {exc}",
        ) from exc


@metadata_router.get("/scoring-models/{scoring_model_name}", response_model=ScoringModelRead)
def get_metadata_scoring_model(
    scoring_model_name: str,
    db: Session = Depends(get_db),
) -> CdpScoringModel:
    obj = _scoring_model_crud.get(db, scoring_model_name)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpScoringModel '{scoring_model_name}' not found")
    return obj


@metadata_router.post("/scoring-models", response_model=ScoringModelRead, status_code=201)
def create_metadata_scoring_model(
    payload: ScoringModelCreate,
    db: Session = Depends(get_db),
) -> CdpScoringModel:
    existing = _scoring_model_crud.get(db, payload.scoring_model_name)
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail=f"CdpScoringModel '{payload.scoring_model_name}' already exists",
        )
    data = payload.model_dump()
    return _scoring_model_crud.create(db, data)


@metadata_router.patch("/scoring-models/{scoring_model_name}", response_model=ScoringModelRead)
def update_metadata_scoring_model(
    scoring_model_name: str,
    payload: ScoringModelUpdate,
    db: Session = Depends(get_db),
) -> CdpScoringModel:
    obj = _scoring_model_crud.get(db, scoring_model_name)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpScoringModel '{scoring_model_name}' not found")
    data = payload.model_dump(exclude_unset=True)
    return _scoring_model_crud.update(db, obj, data)


@metadata_router.delete("/scoring-models/{scoring_model_name}", status_code=204)
def delete_metadata_scoring_model(
    scoring_model_name: str,
    db: Session = Depends(get_db),
) -> None:
    obj = _scoring_model_crud.get(db, scoring_model_name)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"CdpScoringModel '{scoring_model_name}' not found")
    _scoring_model_crud.delete(db, obj)


all_metadata_routers = [metadata_router]
