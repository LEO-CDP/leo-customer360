"""FastAPI composition root for the Customer 360 admin frontend."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app_utils.config import FrontendSettings, load_environment
from app_utils.docs_proxy import DocsProxy, register_docs_routes
from app_utils.lifecycle import SecurityHeadersMiddleware, create_lifespan
from app_utils.page import AdminPageRenderer
from app_utils.static_assets import mount_static_assets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent
load_environment(BASE_DIR)
settings = FrontendSettings.from_environment(BASE_DIR)

app = FastAPI(
    title="Customer 360 Admin Frontend",
    description=(
        "Static Customer 360 admin UI entrypoint. Serves single-page HTML application "
        "and injects runtime configuration constants directly into the template context."
    ),
    version="1.0.0",
    lifespan=create_lifespan(settings),
)
app.add_middleware(SecurityHeadersMiddleware)

page_renderer = AdminPageRenderer(settings)
docs_proxy = DocsProxy(settings)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    return await page_renderer.render(request)


@app.get("/health", tags=["Health"])
async def health():
    return {
        "service": "customer360-frontend",
        "status": "ok",
        "api_base": settings.api_base,
        "environment": "development" if settings.is_dev else "production",
    }


register_docs_routes(app, docs_proxy, settings.root_path)
mount_static_assets(app, settings)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.is_dev,
        log_level="info",
    )
