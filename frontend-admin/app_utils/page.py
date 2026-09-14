"""Admin page rendering."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from .config import FrontendSettings

_log = logging.getLogger("frontend-admin")


class AdminPageRenderer:
    """Render the admin shell with runtime API and asset configuration."""

    def __init__(self, settings: FrontendSettings) -> None:
        templates_dir = settings.base_dir / "base-templates"
        if not templates_dir.exists():
            _log.warning("Templates directory not found at %s", templates_dir)
            templates_dir.mkdir(parents=True, exist_ok=True)
        self.settings = settings
        self.templates = Jinja2Templates(directory=str(templates_dir))

    async def render(self, request: Request):
        cache_bust = (
            self.settings.app_start_time
            if not self.settings.is_dev
            else datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        )
        tenant_id = (
            self.settings.tenant_id
            or request.cookies.get("tenant_id")
            or request.headers.get("X-Tenant-ID")
        )
        context: dict[str, Any] = {
            "request": request,
            "api_base": self.settings.api_base,
            "tenant_id": tenant_id,
            "static_base": self.settings.static_base,
            "cache_bust": cache_bust,
            "build_version": self.settings.build_version,
            "leo_observer_log_domain": self.settings.observer_log_domain,
            "leo_observer_tracking_uri": self.settings.observer_tracking_uri,
            "leo_observer_tracking_endpoint": self.settings.observer_tracking_endpoint,
            "leo_observer_cdn_js": self.settings.observer_cdn_js,
            "docs_ai_base": self.settings.docs_ai_base,
            "docs_site_base": self.settings.docs_site_base,
        }

        try:
            return self.templates.TemplateResponse(request, "index.html", context)
        except Exception as exc:  # noqa: BLE001
            _log.error("Failed to render index template: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to render administration console layout."},
            )
