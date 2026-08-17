import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_export_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register export-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"export", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def export_portfolio(
        accounts: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of account IDs to filter by",
            ),
        ] = None,
        activity_ids: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of activity IDs to filter by",
            ),
        ] = None,
        activity_types: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of activity types to filter by (e.g., BUY, SELL, DIVIDEND)",
            ),
        ] = None,
        asset_classes: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of asset classes to filter by",
            ),
        ] = None,
        data_source: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional data source to filter by (e.g. 'YAHOO', 'COINGECKO')",
            ),
        ] = None,
        symbol: Annotated[
            str | None,
            Field(default=None, description="Optional symbol/ticker to filter by"),
        ] = None,
        tags: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional comma-separated list of tags to filter by",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Export portfolio activities/transactions data as JSON.

        Retrieves portfolio transactions with optional query filters.

        Args:
            accounts: Comma-separated list of account IDs
            activity_ids: Comma-separated list of activity IDs
            activity_types: Comma-separated list of activity types (BUY, SELL, etc.)
            asset_classes: Comma-separated list of asset classes
            data_source: Filter by data source
            symbol: Filter by symbol/ticker
            tags: Comma-separated list of tags

        Returns:
            Dictionary containing the exported activities list.
        """
        async with get_ghostfolio_client(config) as client:
            params: dict[str, Any] = {}
            if accounts:
                params["accounts"] = accounts
            if activity_ids:
                params["activityIds"] = activity_ids
            if activity_types:
                params["activityTypes"] = activity_types
            if asset_classes:
                params["assetClasses"] = asset_classes
            if data_source:
                params["dataSource"] = data_source
            if symbol:
                params["symbol"] = symbol
            if tags:
                params["tags"] = tags

            return await client.get("export", params=params)
