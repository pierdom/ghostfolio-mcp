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

    @mcp.tool(
        tags={"account", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_account_details(
        account_id: Annotated[
            str,
            Field(description="Account ID to retrieve details for"),
        ],
    ) -> dict[str, Any]:
        """
        Get details for a specific account.

        Args:
            account_id: The unique ID of the account to fetch

        Returns:
            Dictionary containing the detailed account profile
        """
        async with get_ghostfolio_client(config) as client:
            return await client.get(f"account/{account_id}")

    @mcp.tool(
        tags={"account", "update"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def update_account(
        account_id: Annotated[
            str,
            Field(description="Account ID to update"),
        ],
        name: Annotated[
            str | None,
            Field(default=None, description="Optional new name of the account"),
        ] = None,
        currency: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional new currency code for the account (e.g., 'USD', 'EUR')",
            ),
        ] = None,
        balance: Annotated[
            float | None,
            Field(default=None, description="Optional new balance for the account"),
        ] = None,
        comment: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional new comment or note for the account",
            ),
        ] = None,
        platform_id: Annotated[
            str | None,
            Field(
                default=None,
                description="Optional new platform ID for the account",
            ),
        ] = None,
        is_excluded: Annotated[
            bool | None,
            Field(
                default=None,
                description="Optional boolean to exclude/include in portfolio calculations",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """
        Update settings or details of an existing account.

        Args:
            account_id: The unique ID of the account to update
            name: Optional new account name
            currency: Optional new account currency
            balance: Optional new cash balance
            comment: Optional new note/comment
            platform_id: Optional new platform ID
            is_excluded: Optional new calculation exclusion boolean

        Returns:
            Dictionary containing the updated account status
        """
        async with get_ghostfolio_client(config) as client:
            account_data: dict[str, Any] = {}
            if name is not None:
                account_data["name"] = name
            if currency is not None:
                account_data["currency"] = currency
            if balance is not None:
                account_data["balance"] = balance
            if comment is not None:
                account_data["comment"] = comment
            if is_excluded is not None:
                account_data["isExcluded"] = is_excluded
            if platform_id is not None:
                account_data["platformId"] = platform_id

            return await client.put(f"account/{account_id}", data=account_data)

    @mcp.tool(
        tags={"account", "transfer"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
        },
    )
    async def transfer_account_balance(
        account_id_from: Annotated[
            str,
            Field(description="The source account ID to transfer cash from"),
        ],
        account_id_to: Annotated[
            str,
            Field(description="The target account ID to transfer cash to"),
        ],
        balance: Annotated[
            float,
            Field(description="The amount of cash to transfer"),
        ],
    ) -> dict[str, Any]:
        """
        Transfer cash balances between two accounts.

        Args:
            account_id_from: Source account ID
            account_id_to: Target account ID
            balance: Amount to transfer

        Returns:
            Dictionary containing the status/result of the transfer.
        """
        async with get_ghostfolio_client(config) as client:
            payload = {
                "accountIdFrom": account_id_from,
                "accountIdTo": account_id_to,
                "balance": balance,
            }
            return await client.post("account/transfer-balance", data=payload)
