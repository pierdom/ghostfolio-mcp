import logging
from typing import Any

from fastmcp import FastMCP

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_system_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register system-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"system", "health", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_health() -> dict[str, Any]:
        """
        Get system health status.

        Retrieves the health status of the Ghostfolio backend service.
        This is useful to verify if the server is up and running correctly.

        Returns:
            Dictionary containing health status information
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("health")

    @mcp.tool(
        tags={"system", "platforms", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_platforms() -> dict[str, Any]:
        """
        Get available platforms.

        Retrieves a list of all available platforms (brokers, exchanges, etc.)
        that can be used when tracking accounts or transactions.

        Returns:
            Dictionary containing available platforms
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("platforms")
