"""Factory for the isolated MCP sub-application."""

from typing import cast

from fastapi import Depends, FastAPI, Request
from fastmcp import FastMCP
from starlette.types import ASGIApp

from core.config import settings
from core.mcptools import register_mcp_tools
from core.mcptools.context import bind_tenant_context, clear_tenant_context, restore_tenant_context


MCP_SERVER_NAME = "LeoCustomer360MCP"


# Re-export as private aliases for test overrides and backward compatibility.
_bind_tenant_context = bind_tenant_context


def _attach_mcp_routes(mcp_server: FastMCP, target_app: FastAPI) -> None:
    """Attach FastMCP endpoints across FastMCP version differences."""
    attach_fn = getattr(mcp_server, "attach", None)
    if callable(attach_fn):
        attach_fn(target_app)
        return

    get_starlette_app_fn = getattr(mcp_server, "get_starlette_app", None)
    if callable(get_starlette_app_fn):
        starlette_app = cast(ASGIApp, get_starlette_app_fn())
        target_app.mount("/", starlette_app)
        return

    raise RuntimeError(
        "FastMCP integration method not found. Expected `attach` or `get_starlette_app`."
    )


def create_mcp_app() -> FastAPI:
    """Create MCP app protected by API key auth and isolated from JWT middleware."""
    mcp = FastMCP(MCP_SERVER_NAME)
    register_mcp_tools(mcp)

    mcp_app = FastAPI(
        title="Customer 360 - MCP Server",
        version=settings.api_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        dependencies=[Depends(_bind_tenant_context)],
    )

    @mcp_app.middleware("http")
    async def _tenant_context_cleanup(request: Request, call_next):
        context_token = clear_tenant_context()
        try:
            return await call_next(request)
        finally:
            restore_tenant_context(context_token)

    _attach_mcp_routes(mcp, mcp_app)

    @mcp_app.get("/health", tags=["MCP Health"])
    def mcp_health() -> dict:
        """Liveness probe for the MCP sub-application."""
        return {"service": "customer360-mcp", "status": "ok"}

    return mcp_app
