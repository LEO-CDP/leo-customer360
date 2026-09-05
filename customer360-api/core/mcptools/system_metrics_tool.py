"""MCP tool: lightweight host metrics.

This tool is intentionally simple and read-only. It is a health/telemetry aid
for MCP clients and should not expose sensitive host details.
"""

from datetime import datetime

import psutil
from fastmcp import FastMCP


def register_system_metrics_tool(mcp: FastMCP) -> None:
    """Register system metrics MCP tool on the provided FastMCP server."""

    @mcp.tool()
    def get_system_metrics() -> dict:
        """Return current server time and memory usage summary."""
        ram = psutil.virtual_memory()
        return {
            "current_date": datetime.now().isoformat(),
            "ram_usage_percent": ram.percent,
            "ram_used_gb": round(ram.used / (1024**3), 2),
            "ram_total_gb": round(ram.total / (1024**3), 2),
        }
