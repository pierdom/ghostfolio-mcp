import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_market_data_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register market-data Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"market-data", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_market_data_for_asset(
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
    ) -> dict[str, Any]:
        """
        Get market data for a specific asset.

        Retrieves current market data for a specific symbol including price,
        volume, market cap, and other relevant market information.

        Args:
            data_source: Data source (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')
            symbol: Symbol/ticker of the asset

        Returns:
            Dictionary containing market data for the specified symbol
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(f"market-data/{data_source}/{symbol}")

    @mcp.tool(
        tags={"market-data", "create"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def add_market_data_points(
        data_source: Annotated[
            str,
            Field(
                description="Data source for the symbol. Typically 'MANUAL' — Ghostfolio rejects market-data writes for auto-fetched sources like 'YAHOO' or 'COINGECKO'"
            ),
        ],
        symbol: Annotated[
            str,
            Field(
                description="Symbol/ticker of the asset (e.g., 'TRUE-UNLISTED', 'PILLAR3A-FINPENSION-X')"
            ),
        ],
        market_data: Annotated[
            list[dict[str, Any]],
            Field(
                description="List of market data points. Each entry must include 'date' (ISO 8601, e.g. '2026-04-30T00:00:00.000Z') and 'marketPrice' (numeric value of the asset at that date)"
            ),
        ],
    ) -> dict[str, Any]:
        """
        Add one or more market data points for a specific asset.

        Posts to the market-data endpoint for the given data source and
        symbol. Same (symbol, date) overwrites the existing point; passing
        the same input twice yields the same end state.

        Args:
            data_source: Data source (typically 'MANUAL')
            symbol: Symbol/ticker of the asset
            market_data: List of {date, marketPrice} entries

        Returns:
            Dictionary containing the upstream response
        """
        async with get_ghostfolio_client(config) as client:
            return await client.post(
                f"market-data/{data_source}/{symbol}",
                data={"marketData": market_data},
            )
