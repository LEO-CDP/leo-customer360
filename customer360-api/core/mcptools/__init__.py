"""MCP tools package.

Add each new MCP tool in its own module and register it here.
"""

from fastmcp import FastMCP

from core.mcptools.search_data_sources_tool import register_search_data_sources_tool
from core.mcptools.system_metrics_tool import register_system_metrics_tool


def register_mcp_tools(mcp: FastMCP) -> None:
    """Register all MCP tools on the provided FastMCP server instance."""
    register_system_metrics_tool(mcp)
    register_search_data_sources_tool(mcp)
