"""Application lifecycle and security middleware."""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware

from .config import FrontendSettings

_log = logging.getLogger("frontend-admin")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security response headers for the admin frontend."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


def create_lifespan(settings: FrontendSettings) -> Callable:
    """Create the FastAPI lifespan handler for the supplied settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _log.info("Initializing Customer 360 Admin Frontend Service...")
        _log.info("Target API Base: %s", settings.api_base)
        _log.info("Tenant ID: %s", settings.tenant_id)
        _log.info("Development Mode: %s", settings.is_dev)
        yield
        _log.info("Shutting down Customer 360 Admin Frontend Service...")

    return lifespan
