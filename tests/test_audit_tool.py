"""Tests for the audit_portfolio_data tool: what it reads from Ghostfolio."""

from datetime import date

import httpx2
import pytest
from fastmcp import FastMCP

from ghostfolio_mcp import ghostfolio_client as client_module
from ghostfolio_mcp.ghostfolio_client import GhostfolioClient
from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.models import InterestRuleConfig
from ghostfolio_mcp.tools import analytics as analytics_module
from ghostfolio_mcp.tools import register_tools

BASE_URL = "https://ghostfolio.test:3333"
AUTH_PATH = "/api/v1/auth/anonymous/"

ACTIVITIES = [
    {
        "id": "b1",
        "type": "BUY",
        "date": "2026-03-29T22:00:00.000Z",
        "accountId": "acc-a",
        "account": {"id": "acc-a", "name": "Broker A"},
        "SymbolProfile": {"symbol": "ETF", "dataSource": "YAHOO"},
        "quantity": 1,
        "unitPrice": 1.1,
        "feeInBaseCurrency": 0,
        "valueInBaseCurrency": 1.1,
        "currency": "EUR",
    },
    {
        "id": "i1",
        "type": "INTEREST",
        "date": "2026-04-01T09:00:00.000Z",
        "accountId": "acc-a",
        "account": {"id": "acc-a", "name": "Broker A"},
        "SymbolProfile": {"symbol": "GF_INTEREST-CASH", "dataSource": "MANUAL"},
        "quantity": 1,
        "unitPrice": 0.01,
        "feeInBaseCurrency": 0,
        "valueInBaseCurrency": 0.01,
        "currency": "EUR",
    },
]

RESPONSES = {
    "/api/v1/activities/": {"activities": ACTIVITIES, "count": len(ACTIVITIES)},
    "/api/v1/account/": {
        "accounts": [
            {"id": "acc-a", "name": "Broker A", "balance": 150.0, "tags": []},
            {
                "id": "acc-b",
                "name": "Bank B",
                "balance": 160.0,
                "tags": [{"name": "EXCLUDE_FROM_ANALYSIS"}],
            },
        ]
    },
    "/api/v1/account/acc-a/balances/": {
        "balances": [{"date": "2026-04-05T00:00:00.000Z", "value": 150.0}]
    },
    "/api/v1/account/acc-b/balances/": {"balances": []},
    "/api/v2/portfolio/performance/": {"chart": []},
    "/api/v1/asset/YAHOO/ETF/": {
        "assetProfile": {"isin": None},
        "marketData": [
            {
                "date": "2026-03-29T00:00:00.000Z",
                "marketPrice": 1.0,
                "isCarriedForward": True,
            },
            {
                "date": "2026-03-30T00:00:00.000Z",
                "marketPrice": 1.1,
                "isCarriedForward": False,
            },
            {
                "date": "2026-04-09T00:00:00.000Z",
                "marketPrice": 1.1,
                "isCarriedForward": False,
            },
        ],
    },
    "/api/v1/user/": {"settings": {"baseCurrency": "EUR"}},
}


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(analytics_module, "_today", lambda _tz: date(2026, 4, 11))
    config = GhostfolioConfig(
        ghostfolio_url=BASE_URL,
        token="api-token",
        account_profiles={
            "acc-a": AccountProfile(interest=InterestRuleConfig(expected="daily"))
        },
    )
    mcp = FastMCP(name="test")
    register_tools(mcp, config)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == AUTH_PATH:
            return httpx2.Response(200, json={"authToken": "jwt"})
        requests.append(request)
        if request.url.path in RESPONSES:
            return httpx2.Response(200, json=RESPONSES[request.url.path])
        return httpx2.Response(404, json={})

    GhostfolioClient._instance = None
    client_module._ghostfolio_client_singleton = None
    client = client_module.get_ghostfolio_client(config)
    client.client = httpx2.AsyncClient(
        base_url=client.base_url, transport=httpx2.MockTransport(handler)
    )
    yield mcp, requests
    GhostfolioClient._instance = None
    client_module._ghostfolio_client_singleton = None


@pytest.mark.asyncio
async def test_audit_reads_only_and_reports_findings(server):
    mcp, requests = server
    tool = await mcp.get_tool("audit_portfolio_data")
    assert tool is not None

    result = (await tool.run({})).structured_content
    assert result is not None

    assert {r.method for r in requests} == {"GET"}
    assert sorted({r.url.path for r in requests}) == sorted(RESPONSES)
    assert result["as_of"] == "2026-04-10"
    found = {(f["check"], f["severity"]) for f in result["findings"]}
    assert ("trade_date_mismatch", "warn") in found
    assert ("missing_interest_day", "warn") in found
    assert ("missing_isin", "info") in found
    cash = [f for f in result["findings"] if f["check"] == "cash_without_interest"]
    assert [f["account_id"] for f in cash] == ["acc-b"]
    assert cash[0]["evidence"]["excluded_from_analysis"] is True
