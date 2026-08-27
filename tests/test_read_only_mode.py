"""READ_ONLY_MODE must be enforced in the shared write path.

Every write tool routes through GhostfolioClient.request(), which refuses any
non-GET/HEAD/OPTIONS call when config.read_only_mode is set - before issuing
any HTTP request, including a read a tool might perform first (see
update_account, which reads the account before it PUTs). These tests drive
each write tool directly and assert both that it raises and that the mocked
transport never saw a single request.
"""

import httpx
import pytest
from fastmcp import FastMCP

from ghostfolio_mcp import ghostfolio_client as client_module
from ghostfolio_mcp.ghostfolio_client import GhostfolioClient
from ghostfolio_mcp.ghostfolio_client import ReadOnlyModeError
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.tools import register_tools

BASE_URL = "https://ghostfolio.test:3333"

# One representative, minimally-valid call per write tool registered anywhere
# in tools/. Keep this in sync with any new write tool - the READ_ONLY_MODE
# guard itself lives in GhostfolioClient.request() and needs no per-tool
# change, but a tool that is never exercised here could still silently regress.
WRITE_TOOL_CASES = [
    ("create_account", {"name": "Test Account", "currency": "USD"}),
    ("update_account", {"account_id": "acc-1", "name": "Renamed"}),
    ("delete_account", {"account_id": "acc-1"}),
    (
        "transfer_account_balance",
        {"account_id_from": "acc-1", "account_id_to": "acc-2", "balance": 10.0},
    ),
    ("create_account_balance", {"account_id": "acc-1", "balance": 100.0}),
    ("delete_account_balance", {"balance_id": "bal-1"}),
    (
        "create_activity",
        {
            "type": "BUY",
            "symbol": "AAPL",
            "date": "2026-01-01T00:00:00.000Z",
            "quantity": 1,
            "unit_price": 100.0,
            "currency": "USD",
            "data_source": "YAHOO",
            "account_id": "acc-1",
        },
    ),
    ("delete_activity", {"activity_id": "act-1"}),
    ("import_transactions", {"data": {"activities": []}}),
    (
        "upsert_asset_profile",
        {
            "data_source": "MANUAL",
            "symbol": "SYM",
            "name": "Some asset",
            "currency": "USD",
            "asset_class": "EQUITY",
        },
    ),
    ("delete_asset_profile", {"data_source": "MANUAL", "symbol": "SYM"}),
    (
        "add_market_data_points",
        {
            "data_source": "MANUAL",
            "symbol": "SYM",
            "market_data": [{"date": "2026-01-01T00:00:00.000Z", "marketPrice": 1.0}],
        },
    ),
    ("add_to_watchlist", {"data_source": "MANUAL", "symbol": "SYM"}),
    ("remove_from_watchlist", {"data_source": "MANUAL", "symbol": "SYM"}),
]


@pytest.fixture
def read_only_tools():
    """A FastMCP server in READ_ONLY_MODE whose transport records every call."""
    config = GhostfolioConfig(
        ghostfolio_url=BASE_URL, token="api-token", read_only_mode=True
    )

    mcp = FastMCP(name="test")
    register_tools(mcp, config)

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"authToken": "jwt"})

    # The client is a singleton twice over - a class attribute and a module
    # global - so reset both to give each test its own transport.
    GhostfolioClient._instance = None
    client_module._ghostfolio_client_singleton = None
    client = client_module.get_ghostfolio_client(config)
    client.client = httpx.AsyncClient(
        base_url=client.base_url, transport=httpx.MockTransport(handler)
    )

    yield mcp, calls

    GhostfolioClient._instance = None
    client_module._ghostfolio_client_singleton = None


async def call(mcp: FastMCP, name: str, arguments: dict):
    tool = await mcp.get_tool(name)
    assert tool is not None, f"tool {name!r} is not registered"
    return await tool.run(arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "arguments"), WRITE_TOOL_CASES)
async def test_write_tool_refuses_under_read_only_mode(
    read_only_tools, name, arguments
):
    mcp, calls = read_only_tools

    with pytest.raises(ReadOnlyModeError, match="READ_ONLY_MODE"):
        await call(mcp, name, arguments)

    assert calls == []


@pytest.mark.asyncio
async def test_read_tool_still_works_under_read_only_mode(read_only_tools):
    mcp, calls = read_only_tools

    await call(mcp, "get_accounts", {})

    # The auth handshake plus the GET itself - reads are unaffected.
    assert calls
