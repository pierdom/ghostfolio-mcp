"""Deterministic benchmark comparison and cash drag over the daily chart.

Pure: no network, no clock, no environment reads. The benchmark is any asset
with stored daily closes; for an accumulating fund its price return is its
total return.

Conventions (``METHOD_VERSION`` changes whenever one of these does):

- Days, flows and the portfolio's TWR are exactly those of
  ``get_monthly_returns``: one chart response, chart (UTC) calendar, flow
  ``F = dV - dNP`` at the start of the day.
- The benchmark close on a chart day is its last stored close on or before
  that day; its return over a chart delta is ``P(d) / P(prev) - 1``.
- ``benchmark_return`` chains those over the month: the benchmark's own
  time-weighted return.
- Same flows: a benchmark holding starts at the portfolio's value before the
  period (at the benchmark close of that day), and every portfolio flow buys
  or sells benchmark units at the previous close, at the start of the day.
  ``benchmark_pnl`` is that holding's P&L, comparable in currency with the
  portfolio's ``market_pnl_net``. Months are measured independently - each
  restarts the holding from the portfolio's own opening value
  (``config.benchmark_reset`` is ``"monthly"``) - while the summary runs one
  holding over the whole period (``summary.benchmark_mode`` is
  ``"continuous"``). The two differ: ``summary.benchmark_pnl`` is the
  continuous holding's P&L, ``summary.benchmark_pnl_sum_of_months`` the sum of
  the monthly ones, and the summary's ``benchmark_units_end`` the continuous
  holding's units.
- Cash drag: on each chart day, the cash held at the end of the previous day
  (chart ``totalCashInBaseCurrency``, which leaves out accounts excluded from
  analysis) times the benchmark's return that day, summed - what the cash
  would have earned in the benchmark. ``cash_drag = opportunity - interest``,
  positive when holding cash cost money against the benchmark. Interest is
  gross and attributed as in ``get_monthly_returns``, from accounts not
  excluded from analysis. Days before ``cash_known_from`` (the first balance
  snapshot) are left out, since the chart shows no cash before then; then
  ``cash.coverage_ratio`` (days counted / chart days) is below 1 and
  ``flags.cash_coverage_partial`` is set.
"""

import math
from collections.abc import Collection
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from typing import Any
from typing import Literal

from ghostfolio_mcp.analytics.common import Activity
from ghostfolio_mcp.analytics.common import ChartRow
from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import money
from ghostfolio_mcp.analytics.common import month_end
from ghostfolio_mcp.analytics.common import month_of
from ghostfolio_mcp.analytics.common import month_start
from ghostfolio_mcp.analytics.common import price_on_or_before
from ghostfolio_mcp.analytics.common import quantity
from ghostfolio_mcp.analytics.common import ratio
from ghostfolio_mcp.analytics.common import shift_month
from ghostfolio_mcp.analytics.monthly_returns import Day
from ghostfolio_mcp.analytics.monthly_returns import _twr
from ghostfolio_mcp.analytics.monthly_returns import chart_window

METHOD_VERSION = "benchmark/2"


@dataclass(frozen=True)
class BenchmarkConfig:
    benchmark: str
    timezone: str = "Europe/Madrid"
    chart_timezone: str = "UTC"
    interest_attribution: Literal["payment_date", "accrual"] = "payment_date"
    interest_rules: Mapping[str, InterestRule] = field(default_factory=dict)
    excluded_accounts: Collection[str] = field(default_factory=frozenset)
    invariant_tolerance: float = 0.01


class _Prices:
    """Benchmark closes looked up on or before a day."""

    def __init__(self, points: list[PricePoint]):
        self.points = sorted(points, key=lambda p: p.date)

    def close(self, day: date) -> float | None:
        point = price_on_or_before(self.points, day)
        return point.price if point else None


def _benchmark_return(days: list[Day], prices: _Prices) -> float | None:
    growth = 1.0
    for day in days:
        before, after = prices.close(day.prev_date), prices.close(day.date)
        if before is None or after is None or before <= 0:
            return None
        growth *= after / before
    return growth - 1.0


def _same_flows(
    days: list[Day], opening_value: float, prices: _Prices
) -> tuple[float | None, float | None, bool]:
    """(benchmark P&L, closing units, units went negative) for these days."""
    if not days:
        return 0.0, None, False
    start = prices.close(days[0].prev_date)
    if start is None or start <= 0:
        return None, None, False
    units = opening_value / start
    pnl: list[float] = []
    negative = False
    for day in days:
        before, after = prices.close(day.prev_date), prices.close(day.date)
        if before is None or after is None or before <= 0:
            return None, None, negative
        units += day.flow / before
        negative = negative or units < -1e-9
        pnl.append(units * (after - before))
    return math.fsum(pnl), units, negative


def _attributed_month(activity: Activity, config: BenchmarkConfig) -> str:
    month = month_of(activity.local_date)
    if config.interest_attribution != "accrual":
        return month
    rule = config.interest_rules.get(activity.account_id, InterestRule())
    return shift_month(month, -1) if rule.attribution == "previous_month" else month


def compare_with_benchmark(
    rows: list[ChartRow],
    benchmark_prices: list[PricePoint],
    activities: list[Activity],
    config: BenchmarkConfig,
    start_month: str,
    end_month: str,
    last_complete_date: date | None = None,
    cash_known_from: date | None = None,
) -> dict[str, Any]:
    """Portfolio vs benchmark per month and over the period, plus cash drag.

    Raises:
        ValueError: On malformed months, a chart without a row before
            ``start_month``, or no benchmark close on or before the baseline.
    """
    window = chart_window(rows, start_month, end_month, last_complete_date)
    prices = _Prices(benchmark_prices)
    if prices.close(window.baseline.date) is None:
        raise ValueError(
            f"{config.benchmark} has no stored close on or before "
            f"{window.baseline.date.isoformat()}"
        )
    tolerance = config.invariant_tolerance

    interest: dict[str, list[float]] = {}
    for activity in activities:
        if (
            activity.type != "INTEREST"
            or activity.account_id in config.excluded_accounts
            or activity.local_date > window.data_end
        ):
            continue
        month = _attributed_month(activity, config)
        if month in window.months:
            interest.setdefault(month, []).append(activity.amount)

    def cash_drag(days: list[Day], interest_paid: float) -> dict[str, Any]:
        counted = [
            day
            for day in days
            if cash_known_from is None or day.prev_date >= cash_known_from
        ]
        opportunity: list[float] = []
        for day in counted:
            before, after = prices.close(day.prev_date), prices.close(day.date)
            if before is None or after is None or before <= 0:
                continue
            opportunity.append(day.prev_cash * (after / before - 1.0))
        earned = math.fsum(opportunity)
        avg_cash = (
            math.fsum(day.prev_cash for day in counted) / len(counted)
            if counted
            else None
        )
        return {
            "avg_cash": money(avg_cash) if avg_cash is not None else None,
            "benchmark_opportunity": money(earned),
            "interest": money(interest_paid),
            "cash_drag": money(earned - interest_paid),
            "days_counted": len(counted),
            "days_before_cash_history": len(days) - len(counted),
            "coverage_ratio": ratio(len(counted) / len(days)) if days else None,
        }

    def measure(
        days: list[Day], opening_value: float, interest_paid: float
    ) -> dict[str, Any]:
        twr, undefined = _twr(days, tolerance)
        bench = _benchmark_return(days, prices)
        pnl = math.fsum(day.d_np for day in days)
        bench_pnl, units, negative = _same_flows(days, opening_value, prices)
        cash = cash_drag(days, interest_paid)
        return {
            "twr": ratio(twr),
            "benchmark_return": ratio(bench),
            "excess_return": ratio(twr - bench)
            if twr is not None and bench is not None
            else None,
            "market_pnl_net": money(pnl),
            "benchmark_pnl": money(bench_pnl) if bench_pnl is not None else None,
            "pnl_difference": money(pnl - bench_pnl) if bench_pnl is not None else None,
            "net_flows": money(math.fsum(day.d_investment for day in days)),
            "benchmark_units_end": quantity(units) if units is not None else None,
            "cash": cash,
            "flags": {
                "undefined_return_days": undefined,
                "benchmark_units_negative": negative,
                "cash_coverage_partial": cash["days_before_cash_history"] > 0,
            },
            "_benchmark_pnl": bench_pnl,
        }

    month_rows = []
    month_pnls: list[float | None] = []
    for month in window.months:
        days = [day for day in window.days if month_of(day.date) == month]
        covered_end = min(month_end(month), window.last_date)
        opening = days[0].prev_value if days else 0.0
        paid = math.fsum(interest.get(month, []))
        measured = measure(days, opening, paid)
        measured["flags"]["partial_month"] = covered_end < month_end(month)
        month_pnls.append(measured.pop("_benchmark_pnl"))
        month_rows.append({"month": month, **measured})

    period_days = [day for day in window.days if month_of(day.date) in window.months]
    period_interest = math.fsum(v for m in window.months for v in interest.get(m, []))
    summary = measure(period_days, window.baseline.value, period_interest)
    summary.pop("_benchmark_pnl")
    summary["benchmark_mode"] = "continuous"
    summary["benchmark_pnl_sum_of_months"] = (
        money(math.fsum(p for p in month_pnls if p is not None))
        if None not in month_pnls
        else None
    )
    summary["flags"]["partial_period"] = min(
        month_end(window.months[-1]), window.last_date
    ) < month_end(end_month)

    return {
        "method_version": METHOD_VERSION,
        "config": {
            "benchmark": config.benchmark,
            "benchmark_reset": "monthly",
            "timezone": config.timezone,
            "chart_timezone": config.chart_timezone,
            "interest_attribution": config.interest_attribution,
            "interest_rules": {
                account_id: {"attribution": rule.attribution, "expected": rule.expected}
                for account_id, rule in sorted(config.interest_rules.items())
            },
            "excluded_accounts": sorted(config.excluded_accounts),
        },
        "window": {
            "start_month": start_month,
            "end_month": window.months[-1],
            "requested_end_month": end_month,
            "baseline_date": window.baseline.date.isoformat(),
            "last_date": window.last_date.isoformat(),
            "dropped_chart_dates": window.dropped,
            "cash_known_from": cash_known_from.isoformat() if cash_known_from else None,
            "window_start": month_start(start_month).isoformat(),
        },
        "months": month_rows,
        "summary": summary,
    }
