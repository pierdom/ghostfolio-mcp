"""Tests for the get_realized_gains tool."""

import pytest
from fastmcp.exceptions import ToolError

from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import TaxProfile


def trade(id_, type_, timestamp, qty, price, account="acc-a", currency="EUR"):
    return {
        "id": id_,
        "type": type_,
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": account.upper()},
        "SymbolProfile": {"symbol": "ETF", "dataSource": "YAHOO"},
        "quantity": qty,
        "unitPrice": price,
        "fee": 0,
        "feeInBaseCurrency": 0,
        "valueInBaseCurrency": qty * price,
        "currency": currency,
    }


def responses(activities):
    return {
        "/api/v1/activities/": {"activities": activities, "count": len(activities)},
        "/api/v1/user/": {"settings": {"baseCurrency": "EUR"}},
    }


@pytest.mark.asyncio
async def test_gains_read_only_and_honour_tax_exclusions(fake_ghostfolio):
    canned = responses(
        [
            trade("p1", "BUY", "2026-01-05T10:00:00Z", 1, 0.1, account="pension"),
            trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0),
            trade("s1", "SELL", "2026-06-10T10:00:00Z", 1, 2.0),
        ]
    )
    call_tool, requests = fake_ghostfolio(
        canned,
        account_profiles={"pension": AccountProfile(tax=TaxProfile(excluded=True))},
    )

    result = await call_tool("get_realized_gains", {"year": 2026})

    assert {r.method for r in requests} == {"GET"}
    assert sorted({r.url.path for r in requests}) == sorted(canned)
    assert result["sales"][0]["gain"] == 1.0
    assert result["config"]["excluded_accounts"] == ["pension"]


@pytest.mark.asyncio
async def test_foreign_currency_trades_are_a_tool_error(fake_ghostfolio):
    call_tool, _requests = fake_ghostfolio(
        responses([trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0, currency="USD")])
    )

    with pytest.raises(ToolError, match="not in EUR"):
        await call_tool("get_realized_gains", {})
