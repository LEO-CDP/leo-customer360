"""Factory for the main Customer 360 HTTP API application."""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from core.auth import EXEMPT_PATHS, auth_middleware
from leo_customer360_dao.config import settings
from core.database import engine
from core.init_core_data import init_core_data
from core.routers.analytics_api import all_analytics_routers
from core.routers.auth_api import all_auth_routers
from core.routers.campaign_draft_api import all_campaign_draft_routers
from core.routers.content_api import all_content_routers
from core.routers.campaign_activation_api import all_campaign_activation_routers
from core.routers.crm_api import all_crm_routers
from core.routers.crm_sync_api import all_crm_sync_routers
from core.routers.events_s3_api import all_events_routers
from core.routers.graph_api import router as graph_router
from core.routers.identity_api import all_identity_routers
from core.routers.metadata_api import metadata_router
from core.routers.data_source_api import all_data_source_routers, data_source_router
from core.routers.ai_agent_api import all_ai_agent_routers, ai_agent_router

from core.routers.persona_api import all_persona_routers
from core.routers.relations_api import all_relations_routers
from core.routers.reporting_api import router as reporting_router
from core.routers.segment_api import all_segment_routers
from core.routers.user_api import all_user_routers
from core.routers.zalo_api import all_zalo_routers


logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
OPENAPI_PUBLIC_PATHS = {
    "/",
    "/health",
    *EXEMPT_PATHS,
}
NORMALIZED_OPENAPI_PUBLIC_PATHS = {
    path.rstrip("/") or "/" for path in OPENAPI_PUBLIC_PATHS
}

# Registration order is part of the API contract: FastAPI evaluates routes in
# that order when static and parameterized paths could both match.
API_ROUTER_GROUPS = (
    (metadata_router,),
    all_data_source_routers,
    all_ai_agent_routers,
    all_identity_routers,
    all_user_routers,
    all_auth_routers,
    (reporting_router,),
    all_relations_routers,
    all_events_routers,
    all_content_routers,
    (graph_router,),
    all_crm_routers,
    all_crm_sync_routers,
    all_campaign_activation_routers,
    all_campaign_draft_routers,
    all_zalo_routers,
    all_segment_routers,
    all_persona_routers,
    all_analytics_routers,
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Detailed production logging middleware for diagnostic tracking.

    Logs request/response method, path, status, latency, client IP,
    reverse proxy headers (X-Forwarded-For/Proto/Host), root_path, and tenant_id.
    Emits WARNING for 404/4xx and ERROR for 5xx with full diagnostic context.
    """

    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        client_ip = request.client.host if request.client else "unknown"
        forwarded_for = request.headers.get("x-forwarded-for", "-")
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", "-"))
        root_path = request.scope.get("root_path", "")

        response = await call_next(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        tenant_id = getattr(request.state, "tenant_id", None) or request.headers.get("x-tenant-id", "-")

        query_info = f" query='{request.url.query}'" if request.url.query else ""
        log_ctx = (
            f"HTTP {request.method} {request.url.path} -> {response.status_code} "
            f"({duration_ms:.2f}ms) | client={client_ip} fwd_for={forwarded_for} "
            f"proto={forwarded_proto} host={forwarded_host} root_path={root_path or '-'} "
            f"tenant={tenant_id}{query_info}"
        )

        if response.status_code == 404:
            logger.warning("404 NOT FOUND: %s", log_ctx)
        elif response.status_code >= 500:
            logger.error("5XX SERVER ERROR: %s", log_ctx)
        elif response.status_code >= 400:
            logger.warning("4XX CLIENT ERROR: %s", log_ctx)
        else:
            logger.info("%s", log_ctx)

        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    has_default_password = bool(settings.default_root_password)
    logger.info(
        "CUSTOMER360-API STARTUP: version=%s environment=%s root_path=%s sso_login=%s",
        settings.api_version,
        settings.environment,
        app.root_path,
        settings.sso_login,
    )
    logger.info(
        "DEFAULT_ROOT_USERNAME=%s DEFAULT_ROOT_PASSWORD_SET=%s",
        settings.default_root_username,
        has_default_password,
    )
    init_core_data()
    route_paths = [getattr(r, "path", "") for r in app.routes if getattr(r, "path", None)]
    logger.info(
        "CUSTOMER360-API READY: %d endpoints registered under prefix '%s'. Root path='%s'.",
        len(route_paths),
        API_PREFIX,
        app.root_path,
    )
    yield
    logger.info("CUSTOMER360-API SHUTDOWN: Cleaning up resources.")


def _include_api_routers(app: FastAPI) -> None:
    """Register all domain routers in dependency-aware order."""
    for router_group in API_ROUTER_GROUPS:
        for router in router_group:
            app.include_router(router, prefix=API_PREFIX)


def _configure_openapi_security(app: FastAPI) -> None:
    """Annotate OpenAPI with bearer auth for protected HTTP routes only."""

    def _custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            description=app.description,
            version=app.version,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})[
            "BearerAuth"
        ] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }

        for path, path_item in schema.get("paths", {}).items():
            normalized_path = path.rstrip("/") or "/"
            if normalized_path in NORMALIZED_OPENAPI_PUBLIC_PATHS:
                continue
            if normalized_path.startswith("/mcp"):
                continue
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("security", [{"BearerAuth": []}])

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = _custom_openapi


def create_http_api_app(mcp_app: FastAPI) -> FastAPI:
    """Create and configure the main Customer 360 HTTP API app."""
    app = FastAPI(
        title="Customer 360 / Identity Resolution API",
        description=(
            "CRUD + reporting API over the customer360 PostgreSQL schema "
            "(core-customer360/database-schema.sql), covering CRM entities and "
            "the full Customer Identity Resolution (CIR) pipeline: raw profile "
            "staging (Adjust/OneSignal/...), master profiles, "
            "profile links, matching-rule metadata, and resolution reporting."
        ),
        version=settings.api_version,
        root_path="/c360api",
        lifespan=_lifespan,
    )

    # Mounted sub-app bypasses parent middleware and keeps MCP auth isolated.
    app.mount("/mcp", mcp_app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(auth_middleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    _configure_openapi_security(app)
    _include_api_routers(app)

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception):
        client_ip = request.client.host if request.client else "unknown"
        forwarded_for = request.headers.get("x-forwarded-for", "-")
        forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", "-"))
        root_path = request.scope.get("root_path", "")
        detail = getattr(exc, "detail", "Not Found")
        logger.warning(
            "ROUTE NOT FOUND (404): method=%s url=%s path=%s root_path=%s query=%s "
            "client_ip=%s forwarded_for=%s proto=%s host=%s detail=%s",
            request.method,
            str(request.url),
            request.url.path,
            root_path,
            request.url.query,
            client_ip,
            forwarded_for,
            forwarded_proto,
            forwarded_host,
            detail,
        )
        return JSONResponse(
            status_code=404,
            content={"detail": detail},
        )

    @app.get("/", tags=["Health"])
    def root() -> dict:
        return {"service": "customer360-api", "status": "ok", "docs": "/docs"}

    @app.get("/health", tags=["Health"])
    def health() -> dict:
        """Verifies the pooled SQLAlchemy engine can actually reach PostgreSQL."""
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "database": "reachable",
            "sso_login": settings.sso_login,
        }

    return app
