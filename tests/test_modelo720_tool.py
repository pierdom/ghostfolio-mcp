"""Tests for the get_modelo720_inputs tool."""

from datetime import date

import pytest
from fastmcp.exceptions import ToolError

from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import Modelo720Profile

RESPONSES = {
    "/api/v1/activities/": {
        "activities": [
            {
                "id": "b1",
                "type": "BUY",
                "date": "2025-03-01T10:00:00Z",
                "accountId": "acc-f",
                "account": {"id": "acc-f", "name": "Foreign"},
                "SymbolProfile": {"symbol": "ETF", "dataSource": "YAHOO"},
                "quantity": 2,
                "unitPrice": 1.0,
                "feeInBaseCurrency": 0,
                "valueInBaseCurrency": 2.0,
                "currency": "EUR",
            }
        ],
        "count": 1,
    },
    "/api/v1/account/": {
        "accounts": [
            {"id": "acc-f", "name": "Foreign", "balance": 1.0, "tags": []},
            {"id": "acc-d", "name": "Domestic", "balance": 1.0, "tags": []},
        ]
    },
    "/api/v1/account/acc-f/balances/": {
        "balances": [{"date": "2025-01-01T00:00:00.000Z", "value": 1.0}]
    },
    "/api/v1/account/acc-d/balances/": {"balances": []},
    "/api/v1/asset/YAHOO/ETF/": {
        "assetProfile": {},
        "marketData": [{"date": "2025-12-31T00:00:00.000Z", "marketPrice": 3.0}],
    },
    "/api/v1/user/": {"settings": {"baseCurrency": "EUR"}},
}


@pytest.mark.asyncio
async def test_reads_only_and_reports_foreign_accounts(fake_ghostfolio):
    call_tool, requests = fake_ghostfolio(
        RESPONSES,
        today=date(2026, 2, 1),
        account_profiles={
            "acc-f": AccountProfile(modelo720=Modelo720Profile(foreign=True))
        },
    )

    result = await call_tool(
        "get_modelo720_inputs", {"year": 2025, "previous_declared_securities": 5.0}
    )

    assert {r.method for r in requests} == {"GET"}
    assert sorted({r.url.path for r in requests}) == sorted(RESPONSES)
    assert [a["account_id"] for a in result["accounts"]] == ["acc-f"]
    assert result["categories"]["securities"]["year_end_value"] == 6.0
    assert result["categories"]["securities"]["increase_over_previous"] == 1.0
    assert result["categories"]["accounts"]["year_end_balance"] == 1.0


@pytest.mark.asyncio
async def test_without_foreign_accounts_it_refuses_before_any_request(fake_ghostfolio):
    call_tool, requests = fake_ghostfolio(RESPONSES)

    with pytest.raises(ToolError, match=r"modelo720\.foreign"):
        await call_tool("get_modelo720_inputs", {"year": 2025})
    assert requests == []
