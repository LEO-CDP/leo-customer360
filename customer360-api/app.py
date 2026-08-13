"""FastAPI application entrypoint for the Customer 360 / Identity Resolution API.

Connects to PostgreSQL via SQLAlchemy 2 ORM using a pooled engine (see
core/database.py: pool_size/max_overflow/pool_recycle/pool_pre_ping configured
from .env). Run with:

    uvicorn app:app --reload

or simply:

    python app.py
"""

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
from core.routers.content_api import all_content_routers
from core.routers.crm_api import all_crm_routers
from core.routers.events_api import all_events_routers
from core.routers.graph_api import router as graph_router
from core.routers.identity_api import all_identity_routers
from core.routers.persona_api import all_persona_routers
from core.routers.relations_api import all_relations_routers
from core.routers.reporting_api import router as reporting_router
from core.routers.segment_api import all_segment_routers
from core.routers.metadata_api import all_metadata_routers
from core.routers.user_api import all_user_routers
from core.routers.auth_api import all_auth_routers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    password_preview = settings.default_root_password[:4] if settings.default_root_password else ""
    logger.info(
        "DEFAULT_ROOT_USERNAME=%s DEFAULT_ROOT_PASSWORD_PREFIX=%s",
        settings.default_root_username,
        password_preview,
    )
    init_core_data()
    yield


app = FastAPI(
    title="Customer 360 / Identity Resolution API",
    description=(
        "CRUD + reporting API over the customer360 PostgreSQL schema "
        "(core-customer360/database-schema.sql), covering CRM entities and "
        "the full Customer Identity Resolution (CIR) pipeline: raw profile "
        "staging (AppsFlyer/MoEnger/...), master profiles, "
        "profile links, matching-rule metadata, and resolution reporting."
    ),
    version=settings.api_version,
    root_path="/c360api",
    lifespan=lifespan,
)

# Permissive CORS for local dev so the static frontend-admin HTML (opened via
# file:// or a plain dev static server on a different origin/port) can call
# this API. Not used with credentials, so allow_origins="*" is safe here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(auth_middleware)

# Declares the Bearer scheme in the OpenAPI schema (purely documentation --
# actual enforcement stays in auth_middleware) so /docs renders an
# "Authorize" button: paste the access_token from POST /auth/login (dev) or
# the Keycloak token from the frontend SSO flow (prod) to call any protected
# endpoint straight from Swagger UI. See customer360-api.md "Authentication".
def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, description=app.description, version=app.version, routes=app.routes)
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.setdefault("security", [{"BearerAuth": []}])
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _custom_openapi

# CIR core models first (primary focus of this API), then supporting CRM /
# relations / graph entities.
for r in all_identity_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_user_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_auth_routers:
    app.include_router(r, prefix="/api/v1")
app.include_router(reporting_router, prefix="/api/v1")
for r in all_relations_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_events_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_content_routers:
    app.include_router(r, prefix="/api/v1")
app.include_router(graph_router, prefix="/api/v1")
for r in all_crm_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_segment_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_metadata_routers:
    app.include_router(r, prefix="/api/v1")
for r in all_persona_routers:
    app.include_router(r, prefix="/api/v1")

@app.get("/", tags=["Health"])
def root():
    return {"service": "customer360-api", "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health():
    """Verifies the pooled SQLAlchemy engine can actually reach PostgreSQL."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "reachable", "sso_login": settings.sso_login}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8008, reload=True)
