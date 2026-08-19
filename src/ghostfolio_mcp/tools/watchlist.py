import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.utils import quote_path_segment

logger = logging.getLogger(__name__)


def register_watchlist_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register watchlist-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"watchlist", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_watchlist() -> dict[str, Any]:
        """
        Get all items in the user's watchlist.

        Retrieves list of all watchlisted symbols.

        Returns:
            Dictionary containing the watchlist items.
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("watchlist")

    @mcp.tool(
        tags={"watchlist"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def add_to_watchlist(
        data_source: Annotated[
            str,
            Field(
                description="Data source for the symbol (e.g. 'YAHOO', 'COINGECKO', 'MANUAL')"
            ),
        ],
        symbol: Annotated[
            str,
            Field(description="Symbol/ticker of the asset to add to the watchlist"),
        ],
    ) -> dict[str, Any]:
        """
        Add a symbol to the user's watchlist.

        Args:
            data_source: Data source (e.g., 'YAHOO', 'COINGECKO')
            symbol: Symbol/ticker of the asset

        Returns:
            Dictionary containing the updated watchlist item information.
        """
        async with get_ghostfolio_client(config) as client:
            return await client.post(
                "watchlist", data={"dataSource": data_source, "symbol": symbol}
            )

    @mcp.tool(
        tags={"watchlist"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
        },
    )
    async def remove_from_watchlist(
        data_source: Annotated[
            str,
            Field(
                description="Data source for the symbol (e.g. 'YAHOO', 'COINGECKO', 'MANUAL')"
            ),
        ],
        symbol: Annotated[
            str,
            Field(
                description="Symbol/ticker of the asset to remove from the watchlist"
            ),
        ],
    ) -> dict[str, Any]:
        """
        Remove a symbol from the user's watchlist.

        Args:
            data_source: Data source (e.g., 'YAHOO', 'COINGECKO')
            symbol: Symbol/ticker of the asset to remove

        Returns:
            Dictionary containing the deletion status.
        """
        async with get_ghostfolio_client(config) as client:
            return await client.delete(
                f"watchlist/{quote_path_segment(data_source)}"
                f"/{quote_path_segment(symbol)}"
            )
