import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_exchange_rates_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register exchange rate Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"exchange-rate", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_exchange_rate(
        symbol: Annotated[
            str,
            Field(
                description="Currency symbol to get exchange rate for (e.g. 'USD', 'CHF', 'EUR')"
            ),
        ],
        date: Annotated[
            str,
            Field(description="Date in YYYY-MM-DD format to retrieve the rate for"),
        ],
    ) -> dict[str, Any]:
        """
        Get the exchange rate for a given currency symbol on a specific date.

        Args:
            symbol: Currency code (e.g. 'USD', 'EUR')
            date: Date in YYYY-MM-DD format

        Returns:
            Dictionary containing the exchange rate value.
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(f"exchange-rate/{symbol}/{date}")
