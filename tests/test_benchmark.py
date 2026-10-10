"""Tests for the benchmark comparison and cash drag. Synthetic, tiny data."""

import json
from datetime import date
from datetime import timedelta

import pytest

from ghostfolio_mcp.analytics.benchmark import METHOD_VERSION
from ghostfolio_mcp.analytics.benchmark import BenchmarkConfig
from ghostfolio_mcp.analytics.benchmark import compare_with_benchmark
from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import parse_activities
from ghostfolio_mcp.analytics.common import parse_chart

TZ = "Europe/Madrid"
BENCH = "YAHOO:IDX"


def chart(start, moves, value=1.0, investment=1.0, cash=0.0):
    """Daily rows from ``start``; each move is (pnl, flow_at_cost[, cash])."""
    day = date.fromisoformat(start)
    net_performance = 0.0
    rows = [_row(day, net_performance, value, investment, cash)]
    for move in moves:
        pnl, flow = move[0], move[1]
        cash = move[2] if len(move) > 2 else cash
        day += timedelta(days=1)
        net_performance += pnl
        investment += flow
        value += pnl + flow
        rows.append(_row(day, net_performance, value, investment, cash))
    return rows


def _row(day, net_performance, value, investment, cash):
    return {
        "date": day.isoformat(),
        "netPerformanceWithCurrencyEffect": net_performance,
        "valueWithCurrencyEffect": value,
        "totalInvestmentValueWithCurrencyEffect": investment,
        "totalCashInBaseCurrency": cash,
    }


def closes(start, values):
    day = date.fromisoformat(start)
    return [PricePoint(day + timedelta(days=i), v) for i, v in enumerate(values)]


def interest(id_, timestamp, amount, account="acc-a"):
    return {
        "id": id_,
        "type": "INTEREST",
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": account},
        "SymbolProfile": {"symbol": "GF_INTEREST-CASH", "dataSource": "MANUAL"},
        "quantity": 1,
        "unitPrice": amount,
        "valueInBaseCurrency": amount,
        "feeInBaseCurrency": 0,
    }


def run(rows, prices, activities=(), start="2026-04", end="2026-04", **kwargs):
    config_keys = {"interest_attribution", "interest_rules", "excluded_accounts"}
    config = {k: kwargs.pop(k) for k in list(kwargs) if k in config_keys}
    return compare_with_benchmark(
        parse_chart(rows),
        prices,
        parse_activities(activities, TZ, "UTC"),
        BenchmarkConfig(benchmark=BENCH, timezone=TZ, **config),
        start,
        end,
        **kwargs,
    )


def flat(n):
    return [(0.0, 0.0)] * n


def test_portfolio_tracking_the_benchmark_has_no_excess():
    # Benchmark doubles over April in one step on Apr 10; so does the portfolio.
    moves = flat(30)
    moves[9] = (1.0, 0.0)
    prices = closes("2026-03-31", [1.0] * 10 + [2.0] * 21)
    [month] = run(chart("2026-03-31", moves), prices)["months"]

    assert month["twr"] == 1.0
    assert month["benchmark_return"] == 1.0
    assert month["excess_return"] == 0.0
    assert month["market_pnl_net"] == 1.0
    assert month["benchmark_pnl"] == 1.0
    assert month["pnl_difference"] == 0.0


def test_flows_buy_benchmark_units_at_the_previous_close():
    # Opening value 1.00 at close 1.00 -> 1 unit. On Apr 11 the portfolio adds
    # 2.00 at the start of the day: 2 units at Apr 10's close of 1.00. The
    # benchmark then rises to 1.50 on Apr 20: 3 units x 0.50 = 1.50.
    moves = flat(30)
    moves[10] = (0.0, 2.0)
    prices = closes("2026-03-31", [1.0] * 20 + [1.5] * 11)
    [month] = run(chart("2026-03-31", moves), prices)["months"]

    assert month["benchmark_pnl"] == 1.5
    assert month["benchmark_units_end"] == 3.0
    assert month["market_pnl_net"] == 0.0
    assert month["pnl_difference"] == -1.5
    assert month["benchmark_return"] == 0.5


def test_sells_reduce_units_and_overselling_is_flagged():
    moves = flat(30)
    moves[4] = (0.0, -3.0)  # more than the 1.00 benchmark holding
    [month] = run(
        chart("2026-03-31", moves, value=4.0, investment=4.0),
        closes("2026-03-31", [4.0] * 31),
    )["months"]

    # 4.00 opening value at 4.00 -> 1 unit; selling 3.00 at 4.00 leaves 0.25.
    assert month["benchmark_units_end"] == 0.25
    assert month["flags"]["benchmark_units_negative"] is False

    moves[4] = (0.0, -5.0)
    [month] = run(
        chart("2026-03-31", moves, value=4.0, investment=4.0),
        closes("2026-03-31", [4.0] * 31),
    )["months"]
    assert month["flags"]["benchmark_units_negative"] is True


def test_weekend_and_missing_closes_use_the_last_close_before():
    prices = [
        PricePoint(date(2026, 3, 31), 1.0),
        PricePoint(date(2026, 4, 15), 1.2),
    ]
    [month] = run(chart("2026-03-31", flat(30)), prices)["months"]

    assert month["benchmark_return"] == 0.2


def test_cash_drag_is_opportunity_minus_interest():
    # 2.00 of cash all month; the benchmark gains 10% on Apr 10.
    rows = chart("2026-03-31", flat(30), cash=2.0)
    prices = closes("2026-03-31", [1.0] * 10 + [1.1] * 21)
    [month] = run(
        rows,
        prices,
        [interest("i1", "2026-04-20T10:00:00Z", 0.05)],
    )["months"]
    cash = month["cash"]

    assert cash["avg_cash"] == 2.0
    assert cash["benchmark_opportunity"] == 0.2
    assert cash["interest"] == 0.05
    assert cash["cash_drag"] == 0.15
    assert cash["days_counted"] == 30


def test_days_before_cash_history_are_left_out():
    moves = [(0.0, 0.0, 0.0)] * 9 + [(0.0, 0.0, 2.0)] * 21  # cash appears Apr 10
    rows = chart("2026-03-31", moves)
    prices = closes("2026-03-31", [1.0 + 0.01 * i for i in range(31)])
    [month] = run(rows, prices, cash_known_from=date(2026, 4, 10))["months"]

    assert month["cash"]["days_counted"] == 20  # Apr 11..30 have a known prior day
    assert month["cash"]["days_before_cash_history"] == 10


def test_interest_of_excluded_accounts_and_accrual():
    rows = chart("2026-03-31", flat(61), cash=1.0)
    prices = closes("2026-03-31", [1.0] * 62)
    activities = [
        interest("i1", "2026-04-30T22:00:00Z", 0.04),  # May 1 local
        interest("i2", "2026-04-15T10:00:00Z", 0.07, account="acc-x"),
    ]
    april, may = run(
        rows,
        prices,
        activities,
        end="2026-05",
        interest_attribution="accrual",
        interest_rules={"acc-a": InterestRule(attribution="previous_month")},
        excluded_accounts={"acc-x"},
    )["months"]

    assert april["cash"]["interest"] == 0.04
    assert may["cash"]["interest"] == 0.0


def test_summary_runs_one_holding_over_the_whole_period():
    # April: benchmark +100%; May: -50%. Per month each starts fresh from the
    # portfolio's own opening value; the summary holds through both.
    moves = flat(61)
    moves[9] = (1.0, 0.0)  # portfolio doubles too (Apr 10)
    moves[39] = (-1.0, 0.0)  # and halves (May 10)
    prices = closes("2026-03-31", [1.0] * 10 + [2.0] * 30 + [1.0] * 22)
    result = run(chart("2026-03-31", moves), prices, end="2026-05")

    assert [m["benchmark_return"] for m in result["months"]] == [1.0, -0.5]
    assert result["summary"]["benchmark_return"] == 0.0
    assert result["summary"]["benchmark_pnl"] == 0.0
    assert result["summary"]["twr"] == 0.0


def test_no_benchmark_close_before_the_window_is_refused():
    with pytest.raises(ValueError, match="no stored close"):
        run(chart("2026-03-31", flat(30)), closes("2026-04-05", [1.0] * 26))


def test_output_is_byte_identical_and_versioned():
    rows = chart("2026-03-31", [(0.01, 0.1, 1.0)] * 30)
    prices = closes("2026-03-31", [1.0 + 0.001 * i for i in range(31)])
    first = run(rows, prices)
    second = run(rows[::-1], prices[::-1])

    assert first["method_version"] == METHOD_VERSION
    assert first["config"]["benchmark"] == BENCH
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
