"""Tests for get_benchmark_comparison: benchmark resolution and reads."""

from datetime import date
from datetime import timedelta

import pytest
from fastmcp.exceptions import ToolError


def chart():
    day = date(2026, 3, 31)
    return [
        {
            "date": (day + timedelta(days=i)).isoformat(),
            "netPerformanceWithCurrencyEffect": 0.01 * i,
            "valueWithCurrencyEffect": 1.0 + 0.01 * i,
            "totalInvestmentValueWithCurrencyEffect": 1.0,
            "totalCashInBaseCurrency": 0.5,
        }
        for i in range(40)
    ]


def asset(price):
    return {
        "assetProfile": {},
        "marketData": [{"date": "2026-03-01T00:00:00.000Z", "marketPrice": price}],
    }


RESPONSES = {
    "/api/v2/portfolio/performance/": lambda request: {
        "chart": chart() if request.url.params["range"] != "mtd" else chart()[-5:]
    },
    "/api/v1/activities/": {"activities": [], "count": 0},
    "/api/v1/account/": {"accounts": [{"id": "acc-a", "name": "A", "tags": []}]},
    "/api/v1/account/acc-a/balances/": {
        "balances": [{"date": "2026-04-03T00:00:00.000Z", "value": 0.5}]
    },
    "/api/v1/asset/YAHOO/ARG/": asset(1.0),
    "/api/v1/asset/YAHOO/ENV/": asset(1.0),
    "/api/v1/asset/YAHOO/USER/": asset(1.0),
    "/api/v1/user/": {"settings": {"benchmark": "bm-2"}},
    "/api/v1/info/": {
        "benchmarks": [
            {"id": "bm-1", "dataSource": "YAHOO", "symbol": "OTHER"},
            {"id": "bm-2", "dataSource": "YAHOO", "symbol": "USER"},
        ]
    },
}

ARGS = {"start_month": "2026-04", "end_month": "2026-04"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "default", "expected"),
    [
        ({"benchmark": "YAHOO:ARG"}, "YAHOO:ENV", "YAHOO:ARG"),
        ({}, "YAHOO:ENV", "YAHOO:ENV"),
        ({}, None, "YAHOO:USER"),
    ],
)
async def test_benchmark_is_resolved_in_order(
    fake_ghostfolio, arguments, default, expected
):
    call_tool, requests = fake_ghostfolio(
        RESPONSES, today=date(2026, 5, 10), default_benchmark=default
    )

    result = await call_tool("get_benchmark_comparison", {**ARGS, **arguments})

    assert {r.method for r in requests} == {"GET"}
    assert result["config"]["benchmark"] == expected
    assert result["window"]["chart_range"] == "ytd"
    assert result["window"]["cash_known_from"] == "2026-04-03"
    assert result["months"][0]["benchmark_return"] == 0.0


@pytest.mark.asyncio
async def test_malformed_benchmark_is_refused(fake_ghostfolio):
    call_tool, _requests = fake_ghostfolio(RESPONSES, today=date(2026, 5, 10))

    with pytest.raises(ToolError, match="DATASOURCE:SYMBOL"):
        await call_tool("get_benchmark_comparison", {**ARGS, "benchmark": "VWCE"})
