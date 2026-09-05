"""Shared MCP request tenant context helpers.

This module centralizes tenant binding so all MCP tools can safely read
tenant scope from authentication context instead of caller-provided params.
"""

from contextvars import ContextVar, Token

from fastapi import Depends, HTTPException, Request, status

from core.auth import verify_mcp_api_key


_tenant_context: ContextVar[str | None] = ContextVar("mcp_tenant_id", default=None)


def get_bound_tenant_id() -> str:
    """Return tenant_id bound by the MCP auth dependency for this request."""
    tenant_id = _tenant_context.get()
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing tenant context for MCP request",
        )
    return tenant_id


async def bind_tenant_context(
    request: Request,
    tenant_id: str = Depends(verify_mcp_api_key),
) -> None:
    """Validate MCP API key and bind the resolved tenant id to context."""
    request.state.mcp_tenant_id = tenant_id
    _tenant_context.set(tenant_id)


def clear_tenant_context() -> Token:
    """Initialize an empty tenant context at request start."""
    return _tenant_context.set(None)


def restore_tenant_context(token: Token) -> None:
    """Restore prior context state after request completion."""
    _tenant_context.reset(token)
