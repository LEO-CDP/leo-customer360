"""FastAPI application entrypoint for the Customer 360 admin frontend.

Serves the static single-page admin UI (index.html + static/) -- plain
HTML/CSS/JS using Tailwind, jQuery and Handlebars via CDN.

All profile/business data is fetched client-side, live, from customer360-api.
The dynamic jinja/index.html injects configuration and cache-busting headers.

Run with:
    uvicorn app:app --reload
or:
    python app.py
"""

from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from typing import Dict
from datetime import datetime, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

# Configure logging to output standard formatting for server logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("frontend-admin")

BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env if running standalone during development
env_path = BASE_DIR / ".env"
if env_path.is_file():
    load_dotenv(env_path)

# Environment & Configuration Parsing
SSO_LOGIN = os.getenv("SSO_LOGIN", "false").lower()
IS_DEV = SSO_LOGIN in ("false", "0", "no")

API_HOSTNAME = os.getenv("FRONTEND_API_HOSTNAME", "http://localhost:8008").rstrip("/")
API_BASE = f"{API_HOSTNAME}/api/v1"
TENANT_ID = os.getenv("FRONTEND_TENANT_ID", "11111111-1111-1111-1111-111111111111")
APP_HOST = os.getenv("HOST", "0.0.0.0")
APP_PORT = int(os.getenv("PORT", "8890"))

# Static Asset Cache-Buster (Set at startup to allow static caching during runtime in production)
# Replaced datetime.utcnow() with datetime.now(timezone.utc) to resolve Pylance deprecation warnings
APP_START_TIME = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

# Setup Templates directory mapping
templates_dir = BASE_DIR / "jinja"
if not templates_dir.exists():
    logger.warning(f"Templates directory not found at {templates_dir}. Creating empty directory.")
    templates_dir.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(templates_dir))


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security response headers for the admin frontend."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Modern FastAPI lifespan context manager handling startup and shutdown logic."""
    logger.info("Initializing Customer 360 Admin Frontend Service...")
    logger.info(f"Target API Base: {API_BASE}")
    logger.info(f"Tenant ID: {TENANT_ID}")
    logger.info(f"Development Mode: {IS_DEV}")
    yield
    logger.info("Shutting down Customer 360 Admin Frontend Service...")


app = FastAPI(
    title="Customer 360 Admin Frontend",
    description=(
        "Static Customer 360 admin UI entrypoint. Serves single-page HTML application "
        "and injects runtime configuration constants directly into the template context."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Attach Security Middlewares to harden the static file serving
app.add_middleware(SecurityHeadersMiddleware)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    """Serve the single-page admin UI with server-injected environment parameters."""
    
    cb = APP_START_TIME if not IS_DEV else datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    tenant_id = TENANT_ID or request.cookies.get("tenant_id") or request.headers.get("X-Tenant-ID")
    
    context: Dict[str, str | Request | None] = {
        "request": request,
        "api_base": API_BASE,
        "tenant_id": tenant_id,
        # Force cache bust on every reload if dev, otherwise only on deployment
        # Replaced datetime.utcnow() with datetime.now(timezone.utc)
        "cache_bust": cb,
    }
    
    try:
        return templates.TemplateResponse(
            request,
            "index.html",
            context
        )
    except Exception as err:
        logger.error(f"Failed to render index template: {str(err)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to render administration console layout."}
        )


@app.get("/health", tags=["Health"])
async def health():
    """Health check endpoint to verify web service availability."""
    return {
        "service": "frontend-admin",
        "status": "ok",
        "api_base": API_BASE,
        "environment": "development" if IS_DEV else "production",
    }


# Static directory mounting (Mounted last to allow explicit routes higher precedence)
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
else:
    logger.warning(f"Static Directory missing at path: {static_dir}")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=IS_DEV,
        log_level="info",
    )
# end of file