import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.utils import quote_path_segment

logger = logging.getLogger(__name__)


def register_activities_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register activity/order-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"portfolio", "orders", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_orders(
        account_id: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional account ID to filter orders by specific account",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Get all activities/orders from your portfolio, optionally filtered by account.

        Retrieves a list of all buy/sell orders in your portfolio, optionally
        filtered by a specific account.

        Args:
            account_id: Optional account ID to filter orders by specific account

        Returns:
            Dictionary containing order data with activities and pagination
        """
        async with get_ghostfolio_client(config) as client:
            params = {"accounts": account_id} if account_id else None
            return await client.get("activities", params=params)

    @mcp.tool(
        tags={"portfolio", "activities", "create"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def create_activity(
        type: Annotated[
            str,
            Field(
                description="Type of activity: BUY, SELL, DIVIDEND, INTEREST, FEE, ITEM, LIABILITY"
            ),
        ],
        symbol: Annotated[
            str, Field(description="Symbol profile ID or actual ticker symbol")
        ],
        date: Annotated[
            str,
            Field(
                description="Date in ISO 8601 format (e.g. 2026-05-09T00:00:00.000Z)"
            ),
        ],
        quantity: Annotated[float, Field(description="Number of shares/units")],
        unit_price: Annotated[float, Field(description="Price per unit")],
        currency: Annotated[
            str, Field(description="Currency code for the transaction")
        ],
        data_source: Annotated[
            str, Field(description="Data source (e.g., 'YAHOO', 'COINGECKO', 'MANUAL')")
        ],
        account_id: Annotated[
            str,
            Field(description="The account ID where this activity will be recorded"),
        ],
        fee: Annotated[
            float, Field(default=0.0, description="Optional fee amount")
        ] = 0.0,
        comment: Annotated[str, Field(default="", description="Optional comment")] = "",
    ) -> dict[str, Any]:
        """
        Create a single new transaction/activity.
        """
        async with get_ghostfolio_client(config) as client:
            activity_data = {
                "type": type,
                "symbol": symbol,
                "date": date,
                "quantity": quantity,
                "unitPrice": unit_price,
                "currency": currency,
                "dataSource": data_source,
                "accountId": account_id,
                "fee": fee,
                "comment": comment,
            }
            return await client.post("activities", data=activity_data)

    @mcp.tool(
        tags={"portfolio", "activities", "delete"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def delete_activity(
        activity_id: Annotated[
            str, Field(description="The unique ID of the activity to delete")
        ],
    ) -> dict[str, Any]:
        """
        Delete a single activity/transaction by its ID.
        """
        async with get_ghostfolio_client(config) as client:
            return await client.delete(f"activities/{quote_path_segment(activity_id)}")
