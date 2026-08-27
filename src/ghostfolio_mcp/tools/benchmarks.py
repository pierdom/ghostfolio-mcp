import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.utils import quote_path_segment

logger = logging.getLogger(__name__)


def register_benchmarks_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register benchmark-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"benchmark", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_benchmarks() -> dict[str, Any]:
        """
        Get all configured benchmarks.

        Returns:
            Dictionary containing the list of benchmarks.
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("benchmarks")

    @mcp.tool(
        tags={"benchmark", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_benchmark_performance(
        data_source: Annotated[
            str,
            Field(
                description="Data source for the benchmark (e.g. 'YAHOO', 'COINGECKO')"
            ),
        ],
        symbol: Annotated[
            str,
            Field(description="Symbol/ticker of the benchmark (e.g. 'URTH', 'AAPL')"),
        ],
        start_date: Annotated[
            str,
            Field(description="Start date in YYYY-MM-DD format for comparison"),
        ],
        date_range: Annotated[
            str,
            Field(
                default="max",
                description="Time range. Options: 1d, 1w, 1m, 3m, 6m, 1y, 2y, 5y, max",
            ),
        ] = "max",
        accounts: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of account IDs to filter by",
            ),
        ] = None,
        asset_classes: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of asset classes to filter by",
            ),
        ] = None,
        filter_data_source: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional data source to filter by",
            ),
        ] = None,
        filter_symbol: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional symbol/ticker to filter by",
            ),
        ] = None,
        tags: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of tags to filter by",
            ),
        ] = None,
        with_excluded_accounts: Annotated[
            bool,
            Field(
                default=False,
                description="Whether to include excluded accounts in the calculations",
            ),
        ] = False,
    ) -> dict[str, Any]:
        """
        Compare portfolio performance against a benchmark symbol starting from a specific date.

        Args:
            data_source: Benchmark data source (e.g. 'YAHOO')
            symbol: Benchmark symbol (e.g. 'URTH')
            start_date: Comparison start date (YYYY-MM-DD)
            date_range: Time range (e.g., 'max')
            accounts: Comma-separated account IDs
            asset_classes: Comma-separated asset classes
            filter_data_source: Filter data source
            filter_symbol: Filter symbol
            tags: Comma-separated tags
            with_excluded_accounts: Include excluded accounts

        Returns:
            Dictionary containing benchmark comparison performance data.
        """
        async with get_ghostfolio_client(config) as client:
            params: dict[str, Any] = {
                "range": date_range,
                "withExcludedAccounts": "true" if with_excluded_accounts else "false",
            }
            if accounts:
                params["accounts"] = accounts
            if asset_classes:
                params["assetClasses"] = asset_classes
            if filter_data_source:
                params["dataSource"] = filter_data_source
            if filter_symbol:
                params["symbol"] = filter_symbol
            if tags:
                params["tags"] = tags

            return await client.get(
                f"benchmarks/{quote_path_segment(data_source)}"
                f"/{quote_path_segment(symbol)}"
                f"/{quote_path_segment(start_date)}",
                params=params,
            )
