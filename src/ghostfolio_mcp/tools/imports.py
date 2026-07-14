import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_imports_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register import-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"import"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def import_transactions(
        data: Annotated[
            dict[str, Any],
            Field(
                description="Transaction data in the format expected by Ghostfolio API. Should contain an 'activities' list. Each activity must have: 'currency', 'dataSource', 'date' (ISO-8601, e.g. 2021-09-15T00:00:00.000Z), 'quantity', 'symbol', 'type' (BUY, SELL, etc), 'unitPrice', and usually 'fee' (can be 0)."
            ),
        ],
    ) -> dict[str, Any]:
        """
        Import transactions into your portfolio. This is a write operation.

        Imports a batch of transactions (buy/sell orders) into your Ghostfolio
        portfolio. This is useful for bulk importing historical data or
        transactions from other platforms.

        Args:
            data: Transaction data. Must contain an 'activities' list with transaction objects including currency, dataSource, date (ISO-8601), fee, quantity, symbol, type, unitPrice.

        Returns:
            Dictionary containing import result
        """
        async with get_ghostfolio_client(config) as client:
            return await client.post("import", data=data)
