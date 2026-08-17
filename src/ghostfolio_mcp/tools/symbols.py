import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_symbols_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register symbol-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"symbol", "data", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_symbol_data(
        data_source: Annotated[
            str,
            Field(
                description="Data source for the symbol (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')"
            ),
        ],
        symbol: Annotated[
            str,
            Field(description="Symbol/ticker of the asset (e.g., 'AAPL', 'BTC')"),
        ],
    ) -> dict[str, Any]:
        """
        Get symbol data for a specific asset from a data source.

        Retrieves detailed information about a specific symbol including
        current price, market data, and asset information.

        Args:
            data_source: Data source (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')
            symbol: Symbol/ticker of the asset

        Returns:
            Dictionary containing symbol data including price and market information
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(f"symbol/{data_source}/{symbol}")

    @mcp.tool(
        tags={"symbol", "historical", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_historical_data(
        data_source: Annotated[
            str,
            Field(
                description="Data source for the symbol (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')"
            ),
        ],
        symbol: Annotated[
            str,
            Field(description="Symbol/ticker of the asset (e.g., 'AAPL', 'BTC-USD')"),
        ],
        date: Annotated[
            str,
            Field(description="Date in YYYY-MM-DD format for historical data"),
        ],
    ) -> dict[str, Any]:
        """
        Get historical data for a specific symbol on a specific date.

        Retrieves historical market data for a symbol on a specific date,
        including price and volume information.

        Args:
            data_source: Data source (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')
            symbol: Symbol/ticker of the asset
            date: Date in YYYY-MM-DD format

        Returns:
            Dictionary containing historical data for the specified date
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(f"symbol/{data_source}/{symbol}/{date}")

    @mcp.tool(
        tags={"symbol", "lookup", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def lookup_symbols(
        query: Annotated[
            str,
            Field(
                description="Search query for symbol lookup. Can be company name, ticker symbol, or partial match"
            ),
        ],
        include_indices: Annotated[
            bool,
            Field(
                default=False, description="Include market indices in search results"
            ),
        ] = False,
    ) -> dict[str, Any]:
        """
        Search for symbols using a query string.

        Search for financial symbols, stocks, ETFs, and other assets using
        a text query. Optionally include market indices in the results.

        Args:
            query: Search query for symbol lookup
            include_indices: Include market indices in search results

        Returns:
            Dictionary containing search results with matching symbols
        """
        async with get_ghostfolio_client(config) as client:
            params = {"query": query}
            if include_indices:
                params["includeIndices"] = "true"
            return await client.get("symbol/lookup", params=params)
