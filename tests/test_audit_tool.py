"""Tests for the audit_portfolio_data tool: what it reads from Ghostfolio."""

from datetime import date

import pytest

from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import InterestRuleConfig

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


@pytest.mark.asyncio
async def test_audit_reads_only_and_reports_findings(fake_ghostfolio):
    call_tool, requests = fake_ghostfolio(
        RESPONSES,
        today=date(2026, 4, 11),
        account_profiles={
            "acc-a": AccountProfile(interest=InterestRuleConfig(expected="daily"))
        },
    )

    result = await call_tool("audit_portfolio_data", {})

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
