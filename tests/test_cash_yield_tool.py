"""Tests for the get_cash_yield tool: what it reads and how it is wired."""

from datetime import date

import pytest
from fastmcp.exceptions import ToolError

from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import InterestRuleConfig

RESPONSES = {
    "/api/v1/activities/": {
        "activities": [
            {
                "id": "i1",
                "type": "INTEREST",
                "date": "2026-04-30T22:00:00.000Z",  # May 1 local
                "accountId": "acc-a",
                "account": {"id": "acc-a", "name": "Bank A"},
                "SymbolProfile": {"symbol": "GF_INTEREST-CASH", "dataSource": "MANUAL"},
                "quantity": 1,
                "unitPrice": 0.03,
                "valueInBaseCurrency": 0.03,
                "feeInBaseCurrency": 0,
            }
        ],
        "count": 1,
    },
    "/api/v1/account/": {
        "accounts": [{"id": "acc-a", "name": "Bank A", "balance": 3.0, "tags": []}]
    },
    "/api/v1/account/acc-a/balances/": {
        "balances": [{"date": "2026-03-01T00:00:00.000Z", "value": 3.0}]
    },
}


@pytest.mark.asyncio
async def test_cash_yield_reads_only_and_applies_accrual_rules(fake_ghostfolio):
    call_tool, requests = fake_ghostfolio(
        RESPONSES,
        today=date(2026, 5, 10),
        account_profiles={
            "acc-a": AccountProfile(
                interest=InterestRuleConfig(attribution="previous_month")
            )
        },
    )

    result = await call_tool(
        "get_cash_yield",
        {
            "start_month": "2026-04",
            "end_month": "2026-05",
            "interest_attribution": "accrual",
        },
    )
    april, may = result["months"]

    assert {r.method for r in requests} == {"GET"}
    assert sorted({r.url.path for r in requests}) == sorted(RESPONSES)
    assert result["window"]["as_of"] == "2026-05-09"
    assert april["accounts"][0]["interest"] == 0.03
    assert april["accounts"][0]["avg_balance"] == 3.0
    assert april["accounts"][0]["annualized_yield"] == pytest.approx(
        0.03 / 3.0 * 365 / 30, abs=1e-8
    )
    assert may["partial_month"] is True


@pytest.mark.asyncio
async def test_bad_month_is_refused_before_any_request(fake_ghostfolio):
    call_tool, requests = fake_ghostfolio(RESPONSES)

    with pytest.raises(ToolError, match="YYYY-MM"):
        await call_tool(
            "get_cash_yield", {"start_month": "2026-4", "end_month": "2026-05"}
        )
    assert requests == []
