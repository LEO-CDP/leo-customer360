"""FastAPI entrypoint for the CDP data-tracking log service."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.routers.tracking import router as tracking_router

app = FastAPI(
    title="Customer 360 Data Tracking API",
    description="Ingests CDP tracking records into hourly S3-compatible objects.",
    version=settings.api_version,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(tracking_router, prefix="/api/v1")
app.include_router(tracking_router, prefix="/data/api/v1")

BASE_DIR = Path(__file__).resolve().parent
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    c360_sdk_dir = static_dir / "c360-web-sdk"
    cdp_event_proxy_html = c360_sdk_dir / "html"
    if c360_sdk_dir.exists():
        app.mount("/cdp-event-proxy.html", StaticFiles(directory=str(cdp_event_proxy_html), html=True), name="c360-web-sdk")
        app.mount("/cdp-sdk", StaticFiles(directory=str(c360_sdk_dir)), name="cdp-sdk")
        app.mount("/data-tracking-api/static/c360-web-sdk", StaticFiles(directory=str(c360_sdk_dir)), name="data-tracking-c360-web-sdk")
    sandbox_dir = static_dir / "sandbox"
    if sandbox_dir.exists():
        app.mount("/sandbox", StaticFiles(directory=str(sandbox_dir), html=True), name="sandbox")


@app.get("/", tags=["Health"])
def root() -> dict[str, str]:
    return {"service": "data-tracking-api", "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    """Return liveness and the selected object-storage mode."""
    return {"status": "ok", "storage_mode": settings.object_storage_mode}
