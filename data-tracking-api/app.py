"""FastAPI entrypoint for the CDP data-tracking log service."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(tracking_router, prefix="/api/v1")


@app.get("/", tags=["Health"])
def root() -> dict[str, str]:
    return {"service": "data-tracking-api", "status": "ok", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    """Return liveness and the selected object-storage mode."""
    return {"status": "ok", "storage_mode": settings.object_storage_mode}
