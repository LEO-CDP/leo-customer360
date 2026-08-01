
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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from core.cache import get_redis_client
from core.config import settings
from core.database import engine, get_db
from core.models.system import SysDomain, SysTenantDomain
from core.utils.dagster_client import DagsterClient

logger = logging.getLogger(__name__)

metadata_router = APIRouter(prefix="/metadata", tags=["System Metadata"])

# Short timeouts keep the metadata endpoint responsive even when a service
# is down; a slow dependency should not make this endpoint hang.
_CONNECTIVITY_TIMEOUT_SECONDS = 2

# the default tenant created by database-init/init-core-database.sql
DEFAULT_TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")

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


all_metadata_routers = [metadata_router]
