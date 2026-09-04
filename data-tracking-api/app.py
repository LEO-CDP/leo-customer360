"""FastAPI entrypoint for the CDP data-tracking log service."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
c360_sdk_dir = static_dir / "c360-web-sdk"
cdp_event_proxy_html = c360_sdk_dir / "html"
cdp_proxy_file = cdp_event_proxy_html / "cdp-event-proxy.html"


def _serve_proxy_file() -> FileResponse:
    return FileResponse(
        path=str(cdp_proxy_file),
        media_type="text/html",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/cdp-event-proxy.html", response_class=FileResponse, tags=["Web SDK"])
def get_cdp_event_proxy() -> FileResponse:
    return _serve_proxy_file()


@app.get("/cdp-event-proxy.html/", response_class=FileResponse, tags=["Web SDK"])
def get_cdp_event_proxy_slash() -> FileResponse:
    return _serve_proxy_file()


@app.get("/data/cdp-event-proxy.html", response_class=FileResponse, tags=["Web SDK"])
def get_data_cdp_event_proxy() -> FileResponse:
    return _serve_proxy_file()


@app.get("/data/cdp-event-proxy.html/", response_class=FileResponse, tags=["Web SDK"])
def get_data_cdp_event_proxy_slash() -> FileResponse:
    return _serve_proxy_file()


if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.mount("/data/static", StaticFiles(directory=str(static_dir)), name="data-static")
    if c360_sdk_dir.exists():
        app.mount("/cdp-sdk", StaticFiles(directory=str(c360_sdk_dir)), name="cdp-sdk")
        app.mount("/data/cdp-sdk", StaticFiles(directory=str(c360_sdk_dir)), name="data-cdp-sdk")
        app.mount("/data-tracking-api/static/c360-web-sdk", StaticFiles(directory=str(c360_sdk_dir)), name="data-tracking-c360-web-sdk")
    sandbox_dir = static_dir / "sandbox"
    if sandbox_dir.exists():
        app.mount("/sandbox", StaticFiles(directory=str(sandbox_dir), html=True), name="sandbox")
        app.mount("/data/sandbox", StaticFiles(directory=str(sandbox_dir), html=True), name="data-sandbox")


@app.get("/", tags=["Health"])
def root() -> dict[str, str]:
    return {"service": "data-tracking-api", "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    """Return liveness and the selected object-storage mode."""
    return {"status": "ok", "storage_mode": settings.object_storage_mode}
