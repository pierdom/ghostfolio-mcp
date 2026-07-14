import logging
from typing import Annotated
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

logger = logging.getLogger(__name__)


def register_accounts_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register account-related Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"account", "balance", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_account_balances(
        account_id: Annotated[
            str,
            Field(description="Account ID to get balances for"),
        ],
    ) -> dict[str, Any]:
        """
        Get account balances for a specific account.

        Retrieves balance information for a specific account including
        current balance, currency, and balance history.

        Args:
            account_id: Account ID to get balances for

        Returns:
            Dictionary containing account balance information
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(f"account/{account_id}/balances")

    @mcp.tool(
        tags={"accounts", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_accounts() -> dict[str, Any]:
        """
        Get all accounts in your portfolio including account types and balances.

        Retrieves a list of all accounts in your portfolio including account
        types, balances, and account-specific information.

        Returns:
            Dictionary containing account information including accounts list and total value
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get("account")

    @mcp.tool(
        tags={"account", "create"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def create_account(
        name: Annotated[
            str,
            Field(
                description="Name of the account (e.g., 'My Brokerage Account', 'Retirement Fund')"
            ),
        ],
        currency: Annotated[
            str,
            Field(
                description="Currency code for the account (e.g., 'USD', 'EUR', 'GBP')"
            ),
        ],
        balance: Annotated[
            float,
            Field(
                default=0.0,
                description="Initial balance for the account (defaults to 0)",
            ),
        ] = 0.0,
        comment: Annotated[
            str,
            Field(default="", description="Optional comment or note for the account"),
        ] = "",
        platform_id: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional platform ID for the account (e.g., broker or exchange identifier)",
            ),
        ] = None,
        is_excluded: Annotated[
            bool,
            Field(
                default=False,
                description="Whether to exclude this account from portfolio calculations",
            ),
        ] = False,
    ) -> dict[str, Any]:
        """
        Create a new account in your portfolio.

        Creates a new account with the specified name, currency, and optional balance.
        This is useful for organizing your investments across different
        account types or platforms.

        Args:
            name: Account name (required)
            currency: Account currency (required, e.g., 'USD', 'EUR')
            balance: Initial balance for the account (defaults to 0)
            comment: Optional comment or note for the account
            platform_id: Optional platform ID for the account
            is_excluded: Whether to exclude this account from calculations

        Returns:
            Dictionary containing the created account information
        """
        async with get_ghostfolio_client(config) as client:
            account_data = {
                "name": name,
                "currency": currency,
                "balance": balance,
                "comment": comment,
                "isExcluded": is_excluded,
                "platformId": platform_id,
            }
            return await client.post("account", data=account_data)

    @mcp.tool(
        tags={"account", "delete"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
        },
    )
    async def delete_account(
        account_id: Annotated[
            str,
            Field(description="Account ID to delete (e.g., 'cb547e5c-..')"),
        ],
    ) -> dict[str, Any]:
        """
        Delete an existing account from your portfolio.

        Deletes an account specified by its ID. Be careful, this might delete
        associated transactions depending on backend rules!

        Args:
            account_id: Account ID to delete

        Returns:
            Dictionary containing the deletion status
        """
        async with get_ghostfolio_client(config) as client:
            return await client.delete(f"account/{account_id}")
