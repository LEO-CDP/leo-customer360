"""MCP tool: tenant-scoped datasource search.

Searches SysDataSource by name keywords and returns only approved summary
fields for AI assistant usage.
"""

import re
import uuid
from dataclasses import dataclass

from fastmcp import FastMCP
from sqlalchemy import case, func, literal, or_, select

from core.database import SessionLocal
from core.mcptools.context import get_bound_tenant_id
from core.models.system import SysDataSource


DEFAULT_MCP_SEARCH_LIMIT = 5
MAX_MCP_SEARCH_LIMIT = 20
MAX_MCP_KEYWORDS = 8
MAX_MCP_KEYWORD_CHARS = 48


def _tokenize_keywords(keywords: str) -> list[str]:
    """Normalize free-text keywords into deduplicated search tokens."""
    tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", keywords.lower()) if t]
    unique_tokens: list[str] = []
    for token in tokens:
        trimmed_token = token[:MAX_MCP_KEYWORD_CHARS]
        if trimmed_token and trimmed_token not in unique_tokens:
            unique_tokens.append(trimmed_token)
        if len(unique_tokens) >= MAX_MCP_KEYWORDS:
            break
    return unique_tokens


def _escape_like_token(token: str) -> str:
    """Escape wildcard operators so keyword search uses literal matches."""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(slots=True)
class MCPDataSourceSearchService:
    """Tenant-scoped datasource lookup used by MCP tools."""

    def search_by_name(self, tenant_id: str, keywords: str, limit: int) -> dict:
        tenant_uuid = uuid.UUID(tenant_id)
        normalized_limit = max(1, min(limit, MAX_MCP_SEARCH_LIMIT))
        tokens = _tokenize_keywords(keywords)
        if not tokens:
            return {"count": 0, "items": []}

        escaped_tokens = [_escape_like_token(token) for token in tokens]
        name_lc = func.lower(SysDataSource.name)
        like_clauses = [name_lc.like(f"%{token}%", escape="\\") for token in escaped_tokens]
        relevance_score = literal(0)
        for token in escaped_tokens:
            relevance_score = relevance_score + case(
                (name_lc.like(f"%{token}%", escape="\\"), 1),
                else_=0,
            )

        db = SessionLocal()
        try:
            stmt = (
                select(
                    SysDataSource.data_source_id,
                    SysDataSource.name,
                    SysDataSource.total_tracked_event,
                    SysDataSource.avg_daily_event,
                    SysDataSource.avg_events_per_profile,
                    SysDataSource.javascript_tags,
                    SysDataSource.qr_code_data,
                )
                .where(SysDataSource.tenant_id == tenant_uuid)
                .where(or_(*like_clauses))
                .order_by(
                    relevance_score.desc(),
                    SysDataSource.total_tracked_event.desc(),
                    SysDataSource.name.asc(),
                )
                .limit(normalized_limit)
            )
            rows = db.execute(stmt).all()
        finally:
            db.close()

        items = [
            {
                "id": str(row.data_source_id),
                "name": row.name,
                "total_tracked_event": int(row.total_tracked_event or 0),
                "avg_daily_event": int(row.avg_daily_event or 0),
                "avg_events_per_profile": float(row.avg_events_per_profile or 0),
                "javascript_tags": row.javascript_tags or [],
                "qr_code_data": row.qr_code_data or {},
            }
            for row in rows
        ]
        return {"count": len(items), "items": items}


def register_search_data_sources_tool(mcp: FastMCP) -> None:
    """Register datasource search MCP tool on the provided FastMCP server."""
    service = MCPDataSourceSearchService()

    @mcp.tool()
    def search_data_sources_by_name(keywords: str, limit: int = DEFAULT_MCP_SEARCH_LIMIT) -> dict:
        """Search tenant data sources by name using authenticated MCP tenant context."""
        tenant_id = get_bound_tenant_id()
        return service.search_by_name(tenant_id=tenant_id, keywords=keywords, limit=limit)
