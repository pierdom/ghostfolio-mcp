"""Tests for the get_realized_gains tool."""

from datetime import date

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
        "/api/v1/asset/YAHOO/ETF/": {
            "assetProfile": {},
            "marketData": [{"date": "2026-08-31T00:00:00.000Z", "marketPrice": 3.0}],
        },
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
    assert result["open_lots"] == []


@pytest.mark.asyncio
async def test_open_lots_are_valued_and_detailed(fake_ghostfolio):
    canned = responses(
        [
            trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0),
            trade("b2", "BUY", "2026-02-10T10:00:00Z", 1, 2.0),
        ]
    )
    call_tool, requests = fake_ghostfolio(canned, today=date(2026, 9, 2))

    result = await call_tool("get_realized_gains", {"include_open_lot_detail": True})
    [lot] = result["open_lots"]

    assert {r.method for r in requests} == {"GET"}
    assert result["valuation_date"] == "2026-09-01"
    assert (lot["market_value"], lot["unrealized_gain"]) == (6.0, 3.0)
    assert [d["buy_activity_id"] for d in lot["lot_detail"]] == ["b1", "b2"]


@pytest.mark.asyncio
async def test_foreign_currency_trades_are_a_tool_error(fake_ghostfolio):
    call_tool, _requests = fake_ghostfolio(
        responses([trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0, currency="USD")])
    )

    with pytest.raises(ToolError, match="not in EUR"):
        await call_tool("get_realized_gains", {})
