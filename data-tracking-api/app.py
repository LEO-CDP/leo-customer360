"""FastAPI entrypoint for the CDP data-tracking log service."""

from pathlib import Path
from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.config import settings
from core.redis_cache import TrackingRequestProtection
from core.routers.tracking import get_protection, get_storage, router as tracking_router
from core.storage import S3ObjectStorage

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


@app.get("/cdp-sdk/html/cdp-event-proxy.html", response_class=FileResponse, tags=["Web SDK"])
def get_cdp_event_proxy() -> FileResponse:
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
def health(
    response: Response,
    storage: S3ObjectStorage = Depends(get_storage),
    protection: TrackingRequestProtection = Depends(get_protection),
) -> dict[str, str]:
    """Report liveness plus dependency reachability.

    S3 is the critical sink: if it is unreachable the service cannot ingest, so
    /health answers 503 ("error"). Redis (rate limiting + session counters)
    fails open, so an unreachable Redis is reported as "degraded" but keeps a
    200 — ingestion still works without it.
    """
    try:
        storage.check_connection()
        s3_ok = True
    except Exception:
        s3_ok = False

    redis_ok = protection.ping()

    if not s3_ok:
        response.status_code = 503
        status = "error"
    else:
        status = "ok" if redis_ok else "degraded"

    return {
        "status": status,
        "storage_mode": settings.object_storage_mode,
        "s3": "reachable" if s3_ok else "unreachable",
        "redis": "reachable" if redis_ok else "unreachable",
    }
