"""Tests for the get_monthly_returns tool: data fetching and window choice.

The calculation itself is covered in test_monthly_returns.py; these tests pin
what the tool sends to Ghostfolio. All data is synthetic.
"""

from datetime import date
from datetime import timedelta

import httpx2
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from ghostfolio_mcp import ghostfolio_client as client_module
from ghostfolio_mcp.ghostfolio_client import GhostfolioClient
from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_config_from_env
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.models import InterestRuleConfig
from ghostfolio_mcp.tools import analytics as analytics_module
from ghostfolio_mcp.tools import register_tools

BASE_URL = "https://ghostfolio.test:3333"
AUTH_PATH = "/api/v1/auth/anonymous/"
PERFORMANCE_PATH = "/api/v2/portfolio/performance/"
ACTIVITIES_PATH = "/api/v1/activities/"


def daily_chart(first_day: str, last_day: str) -> list[dict]:
    """Flat 1.00 portfolio earning 0.01 a day."""
    day, end = date.fromisoformat(first_day), date.fromisoformat(last_day)
    rows = []
    net_performance = 0.0
    while day <= end:
        rows.append(
            {
                "date": day.isoformat(),
                "netPerformanceWithCurrencyEffect": net_performance,
                "valueWithCurrencyEffect": 1.0 + net_performance,
                "totalInvestmentValueWithCurrencyEffect": 1.0,
            }
        )
        net_performance += 0.01
        day += timedelta(days=1)
    return rows


CHARTS = {
    "mtd": daily_chart("2026-04-30", "2026-05-10"),
    "ytd": daily_chart("2025-12-31", "2026-05-10"),
    "1y": daily_chart("2025-05-10", "2026-05-10"),
}


@pytest.fixture
def server(monkeypatch):
    """A FastMCP server backed by a synthetic Ghostfolio, clock pinned."""
    monkeypatch.setattr(analytics_module, "_today", lambda _tz: date(2026, 5, 10))
    config = GhostfolioConfig(
        ghostfolio_url=BASE_URL,
        token="api-token",
        interest_rules={
            "acc-a": InterestRuleConfig(
                attribution="previous_month", expected="monthly"
            )
        },
    )
    mcp = FastMCP(name="test")
    register_tools(mcp, config)

    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == AUTH_PATH:
            return httpx2.Response(200, json={"authToken": "jwt"})
        requests.append(request)
        if request.url.path == PERFORMANCE_PATH:
            chart = CHARTS[request.url.params["range"]]
            return httpx2.Response(200, json={"chart": chart})
        if request.url.path == ACTIVITIES_PATH:
            interest = {
                "id": "i1",
                "type": "INTEREST",
                "date": "2026-04-30T22:00:00.000Z",
                "accountId": "acc-a",
                "account": {"id": "acc-a", "name": "Broker A"},
                "SymbolProfile": {"symbol": "GF_INTEREST-CASH"},
                "valueInBaseCurrency": 0.03,
                "feeInBaseCurrency": 0,
            }
            return httpx2.Response(200, json={"activities": [interest], "count": 1})
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


async def call(mcp: FastMCP, arguments: dict) -> dict:
    tool = await mcp.get_tool("get_monthly_returns")
    assert tool is not None
    result = (await tool.run(arguments)).structured_content
    assert result is not None
    return result


@pytest.mark.asyncio
async def test_uses_the_smallest_daily_range_that_starts_before_the_window(server):
    mcp, requests = server

    result = await call(mcp, {"start_month": "2026-04", "end_month": "2026-05"})

    assert [(r.method, r.url.path, r.url.params.get("range")) for r in requests] == [
        ("GET", PERFORMANCE_PATH, "mtd"),
        ("GET", PERFORMANCE_PATH, "ytd"),
        ("GET", ACTIVITIES_PATH, None),
    ]
    assert result["window"]["chart_range"] == "ytd"
    assert result["window"]["baseline_date"] == "2026-03-31"


@pytest.mark.asyncio
async def test_current_month_uses_mtd(server):
    mcp, requests = server

    result = await call(mcp, {"start_month": "2026-05", "end_month": "2026-05"})

    assert result["window"]["chart_range"] == "mtd"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_todays_row_is_dropped(server):
    mcp, _requests = server

    result = await call(mcp, {"start_month": "2026-05", "end_month": "2026-05"})

    assert result["window"]["dropped_chart_dates"] == ["2026-05-10"]
    assert result["window"]["last_date"] == "2026-05-09"
    assert result["months"][0]["market_pnl_net"] == 0.09


@pytest.mark.asyncio
async def test_accrual_uses_configured_rules(server):
    mcp, _requests = server

    result = await call(
        mcp,
        {
            "start_month": "2026-04",
            "end_month": "2026-05",
            "interest_attribution": "accrual",
        },
    )
    april, may = result["months"]

    assert april["interest"] == 0.03
    assert may["interest"] == 0.0
    assert may["flags"]["missing_interest_accounts"] == ["acc-a"]
    assert result["config"]["interest_rules"] == {
        "acc-a": {"attribution": "previous_month", "expected": "monthly"}
    }


@pytest.mark.asyncio
async def test_months_without_daily_data_are_refused(server):
    mcp, _requests = server

    with pytest.raises(ToolError, match="No daily chart reaches back before 2025-03"):
        await call(mcp, {"start_month": "2025-03", "end_month": "2025-04"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"start_month": "2026-4", "end_month": "2026-05"}, "YYYY-MM"),
        (
            {"start_month": "2026-04", "end_month": "2026-05", "timezone": "Mars/Base"},
            "timezone",
        ),
    ],
)
async def test_bad_arguments_are_refused_before_any_request(server, arguments, message):
    mcp, requests = server

    with pytest.raises(ToolError, match=message):
        await call(mcp, arguments)
    assert requests == []


def test_interest_rules_are_read_from_env(monkeypatch):
    monkeypatch.setenv(
        "INTEREST_RULES",
        '{"acc-a": {"attribution": "previous_month", "expected": "monthly"}, "acc-b": {}}',
    )

    rules = get_ghostfolio_config_from_env().interest_rules

    assert rules == {
        "acc-a": InterestRuleConfig(attribution="previous_month", expected="monthly"),
        "acc-b": InterestRuleConfig(),
    }


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_interest_rules_mean_none(monkeypatch, value):
    monkeypatch.setenv("INTEREST_RULES", value)

    assert get_ghostfolio_config_from_env().interest_rules == {}


def test_invalid_interest_rule_is_rejected(monkeypatch):
    monkeypatch.setenv("INTEREST_RULES", '{"acc-a": {"attribution": "yearly"}}')

    with pytest.raises(ValueError, match="attribution"):
        get_ghostfolio_config_from_env()
