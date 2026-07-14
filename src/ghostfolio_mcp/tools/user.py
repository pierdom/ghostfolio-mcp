import logging
from typing import Any

from fastmcp import FastMCP

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_user_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register user-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"user", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_user_info() -> dict[str, Any]:
        """
        Get user information and settings.

        Retrieves information about the current user including settings,
        preferences, and account details.

        Returns:
            Dictionary containing user information and settings
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("user")
