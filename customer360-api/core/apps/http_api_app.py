"""Factory for the main Customer 360 HTTP API application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import text

from core.auth import auth_middleware
from core.config import settings
from core.database import engine
from core.init_core_data import init_core_data
from core.routers.analytics_api import all_analytics_routers
from core.routers.auth_api import all_auth_routers
from core.routers.content_api import all_content_routers
from core.routers.crm_api import all_crm_routers
from core.routers.events_api import all_events_routers
from core.routers.graph_api import router as graph_router
from core.routers.identity_api import all_identity_routers
from core.routers.metadata_api import all_metadata_routers
from core.routers.persona_api import all_persona_routers
from core.routers.relations_api import all_relations_routers
from core.routers.reporting_api import router as reporting_router
from core.routers.segment_api import all_segment_routers
from core.routers.user_api import all_user_routers


logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
PUBLIC_PATHS = {
    "/",
    "/health",
    "/api/v1/metadata",
    "/api/v1/auth/login",
    "/api/v1/auth/callback",
    "/api/v1/auth/logout",
    "/mcp",
    "/mcp/",
    "/mcp/health",
}
NORMALIZED_PUBLIC_PATHS = {p.rstrip("/") or "/" for p in PUBLIC_PATHS}


@asynccontextmanager
async def _lifespan(_: FastAPI):
    has_default_password = bool(settings.default_root_password)
    logger.info(
        "DEFAULT_ROOT_USERNAME=%s DEFAULT_ROOT_PASSWORD_SET=%s",
        settings.default_root_username,
        has_default_password,
    )
    init_core_data()
    yield


def _include_api_routers(app: FastAPI) -> None:
    """Register all domain routers in dependency-aware order."""
    for r in all_identity_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_user_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_auth_routers:
        app.include_router(r, prefix=API_PREFIX)
    app.include_router(reporting_router, prefix=API_PREFIX)
    for r in all_relations_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_events_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_content_routers:
        app.include_router(r, prefix=API_PREFIX)
    app.include_router(graph_router, prefix=API_PREFIX)
    for r in all_crm_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_segment_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_metadata_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_persona_routers:
        app.include_router(r, prefix=API_PREFIX)
    for r in all_analytics_routers:
        app.include_router(r, prefix=API_PREFIX)


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
            if normalized_path in NORMALIZED_PUBLIC_PATHS:
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

    _configure_openapi_security(app)
    _include_api_routers(app)

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
