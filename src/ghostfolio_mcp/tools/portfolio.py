import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.utils import quote_path_segment

logger = logging.getLogger(__name__)


def register_portfolio_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register portfolio-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"portfolio", "details", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_portfolio_details() -> dict[str, Any]:
        """
        Get comprehensive portfolio details including accounts, positions, and summary.

        Retrieves a complete overview of your portfolio including account
        information, current positions, performance summary, and portfolio metrics.

        Returns:
            Dictionary containing complete portfolio information including accounts, positions, and summary
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("portfolio/details")

    @mcp.tool(
        tags={"portfolio", "dividends", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_dividends(
        group_by: Annotated[
            str,
            Field(
                default="month",
                description="Grouping period for dividend data. Options: day, week, month, quarter, year",
            ),
        ] = "month",
        date_range: Annotated[
            str,
            Field(
                default="max",
                description="Time range for dividend data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max",
            ),
        ] = "max",
    ) -> dict[str, Any]:
        """
        Get dividend data grouped by time period showing dividend payments and yield.

        Retrieves dividend income data grouped by the specified time period,
        showing dividend payments, yield, and income patterns over time.

        Args:
            group_by: Grouping period: day, week, month, quarter, year
            date_range: Time range for dividend data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max

        Returns:
            Dictionary containing dividend data grouped by the specified period
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(
                "portfolio/dividends",
                params={"range": date_range, "groupBy": group_by},
            )

    @mcp.tool(
        tags={"portfolio", "holdings", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_portfolio_holdings(
        date_range: Annotated[
            str,
            Field(
                default="max",
                description="Time range for holdings data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max",
            ),
        ] = "max",
    ) -> dict[str, Any]:
        """
        Get portfolio holdings and positions including allocations and asset breakdowns.

        Retrieves current portfolio holdings including positions, allocations,
        and asset breakdowns for the specified time period.

        Args:
            date_range: Time range for holdings data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max

        Returns:
            Dictionary containing holdings, accounts, allocations, and range data
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("portfolio/holdings", params={"range": date_range})

    @mcp.tool(
        tags={"portfolio", "investments", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_investments(
        group_by: Annotated[
            str,
            Field(
                default="month",
                description="Grouping period for investment data. Options: day, week, month, quarter, year",
            ),
        ] = "month",
        date_range: Annotated[
            str,
            Field(
                default="max",
                description="Time range for investment data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max",
            ),
        ] = "max",
    ) -> dict[str, Any]:
        """
        Get investment data grouped by time period showing cash flows and contributions.

        Retrieves investment activity data grouped by the specified time period,
        showing cash flows, contributions, and investment patterns over time.

        Args:
            group_by: Grouping period: day, week, month, quarter, year
            date_range: Time range for investment data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max

        Returns:
            Dictionary containing investment data grouped by the specified period
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(
                "portfolio/investments",
                params={"range": date_range, "groupBy": group_by},
            )

    @mcp.tool(
        tags={"portfolio", "performance", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_portfolio_performance(
        date_range: Annotated[
            str,
            Field(
                default="max",
                description="Time range for performance data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max",
            ),
        ] = "max",
    ) -> dict[str, Any]:
        """
        Get portfolio performance data including returns, benchmarks, and performance metrics.

        Retrieves comprehensive performance metrics for your portfolio including
        returns, benchmarks, and performance comparisons over the specified time period.

        Args:
            date_range: Time range for performance data. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max

        Returns:
            Dictionary containing performance metrics, returns, benchmarks, and range data
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(
                "portfolio/performance", params={"range": date_range}, api_version="v2"
            )

    @mcp.tool(
        tags={"portfolio", "positions", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_position(
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
        Get position details for a specific symbol from a data source.

        Retrieves detailed information about a specific position including
        current value, quantity, performance, and market data.

        Args:
            data_source: Data source (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')
            symbol: Symbol/ticker of the asset

        Returns:
            Dictionary containing position details including symbol, quantity, value, and performance
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(
                f"portfolio/holding/{quote_path_segment(data_source)}"
                f"/{quote_path_segment(symbol)}"
            )
