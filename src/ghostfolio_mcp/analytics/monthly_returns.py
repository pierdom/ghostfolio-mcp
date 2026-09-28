"""Deterministic monthly return analytics over Ghostfolio chart rows and activities.

Everything in this module is pure: no network, no clock, no environment reads.
The caller parses the raw Ghostfolio responses with :func:`parse_chart` and
:func:`parse_activities` and hands them to :func:`compute_monthly_returns`,
which returns a JSON-ready dict. Floats are rounded only in that final dict;
every intermediate sum uses :func:`math.fsum` over input sorted by date and id,
so the same inputs always produce byte-identical output.

Conventions (``METHOD_VERSION`` changes whenever one of these does):

- Two calendars. Ghostfolio buckets activities into chart days on its own
  server calendar (``chart_timezone``, UTC by default), and a chart delta cannot
  be re-split after the fact. So everything derived from the chart - net P&L,
  flows, TWR, Modified Dietz - is bucketed by chart row date, and fees are
  mapped onto the same chart days so that ``market_pnl_gross = net + fees``
  stays consistent. Everything reported per activity - interest, sell days,
  the per-account breakdown - is bucketed by the activity's date in
  ``timezone``. Activities whose two months differ are listed in
  ``calendar_mismatches``.
- Daily P&L is ``dNP`` (netPerformance, net of fees, excluding interest).
- Daily external flow is ``F = dV - dNP``: the money that entered or left the
  securities sleeve, valued at market. On ordinary days that equals
  ``dInvestment + fees``; on a sell day it also carries the difference between
  execution and closing price. It is negative for a net sale. ``net_flows`` is
  reported separately, at cost (sum of ``dInvestment``).
- Flows happen at the start of the day, so capital at work on day d is
  ``V[d-1] + F[d]`` and the daily return is ``dNP[d] / (V[d-1] + F[d])``.
- ``avg_invested_capital = V0 + sum(w[d] * F[d])`` with
  ``w[d] = (D - i[d] + 1) / D``, where V0 is the value at the last chart row
  before the window, ``i[d]`` the 1-based calendar day of the window and D the
  number of calendar days covered. Modified Dietz is
  ``market_pnl_net / avg_invested_capital``.
- Invariant, per chart day: ``dNP - (dV - dInvestment) + fees == 0`` within
  ``invariant_tolerance``. It is expected to break on sell days; a break equal
  to that day's interest means Ghostfolio started counting interest inside
  netPerformance. Breaks are reported, never corrected.
"""

import math
import re
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from itertools import pairwise
from typing import Any
from typing import Literal
from zoneinfo import ZoneInfo

METHOD_VERSION = "monthly-returns/1"

SUPPORTED_ACTIVITY_TYPES = frozenset({"BUY", "SELL", "INTEREST"})

# Capital at work at or below this is treated as no capital at all.
_ZERO_CAPITAL = 0.01

_MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")

_MONEY = Decimal("0.01")
_RATIO = Decimal("0.00000001")

InterestAttribution = Literal["payment_date", "accrual"]


@dataclass(frozen=True)
class ChartRow:
    """One daily row of the Ghostfolio v2 performance chart, in base currency."""

    date: date
    net_performance: float
    value: float
    total_investment: float


@dataclass(frozen=True)
class Activity:
    """A Ghostfolio activity with its date resolved on both calendars."""

    id: str
    type: str
    account_id: str
    account_name: str
    symbol: str
    local_date: date
    chart_date: date
    amount: float
    fee: float


@dataclass(frozen=True)
class InterestRule:
    """How one account's interest is attributed under ``accrual``.

    ``attribution="previous_month"`` moves a payment into the month before its
    payment date (an account that pays on the 1st for the month just ended).
    ``expected="monthly"`` makes a month without any interest from the account
    show up in ``missing_interest_accounts``.
    """

    attribution: Literal["payment_date", "previous_month"] = "payment_date"
    expected: Literal["none", "monthly"] = "none"


@dataclass(frozen=True)
class ReturnsConfig:
    timezone: str = "Europe/Madrid"
    chart_timezone: str = "UTC"
    interest_attribution: InterestAttribution = "payment_date"
    interest_rules: Mapping[str, InterestRule] = field(default_factory=dict)
    invariant_tolerance: float = 0.01


@dataclass(frozen=True)
class _Day:
    """The change between two consecutive chart rows, dated at the later one."""

    date: date
    prev_date: date
    prev_value: float
    d_np: float
    d_value: float
    d_investment: float

    @property
    def flow(self) -> float:
        return self.d_value - self.d_np

    @property
    def capital(self) -> float:
        return self.prev_value + self.flow


def parse_chart(raw_chart: Iterable[Mapping[str, Any]]) -> list[ChartRow]:
    """Parse ``chart`` rows of ``GET /api/v2/portfolio/performance``.

    Uses the base-currency (``...WithCurrencyEffect``) fields. Rows must have
    unique dates; they are returned sorted by date.
    """
    rows = [
        ChartRow(
            date=date.fromisoformat(row["date"][:10]),
            net_performance=float(row["netPerformanceWithCurrencyEffect"]),
            value=float(row["valueWithCurrencyEffect"]),
            total_investment=float(row["totalInvestmentValueWithCurrencyEffect"]),
        )
        for row in raw_chart
    ]
    rows.sort(key=lambda row: row.date)
    for prev, cur in pairwise(rows):
        if prev.date == cur.date:
            raise ValueError(f"Duplicate chart date {cur.date.isoformat()}")
    return rows


def _parse_timestamp(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"Activity timestamp without a UTC offset: {value!r}")
    return timestamp


def parse_activities(
    raw_activities: Iterable[Mapping[str, Any]],
    timezone: str,
    chart_timezone: str,
) -> list[Activity]:
    """Parse ``activities`` of ``GET /api/v1/activities``.

    Each timestamp is converted to both ``timezone`` and ``chart_timezone``
    before its date is taken - never sliced or shifted by a fixed offset.
    """
    local_tz = ZoneInfo(timezone)
    chart_tz = ZoneInfo(chart_timezone)
    activities = []
    for raw in raw_activities:
        timestamp = _parse_timestamp(raw["date"])
        account = raw.get("account") or {}
        profile = raw.get("SymbolProfile") or {}
        activities.append(
            Activity(
                id=str(raw["id"]),
                type=str(raw["type"]),
                account_id=str(raw.get("accountId") or account.get("id") or ""),
                account_name=str(account.get("name") or ""),
                symbol=str(profile.get("symbol") or ""),
                local_date=timestamp.astimezone(local_tz).date(),
                chart_date=timestamp.astimezone(chart_tz).date(),
                amount=float(raw["valueInBaseCurrency"]),
                fee=float(raw.get("feeInBaseCurrency") or 0),
            )
        )
    activities.sort(key=lambda activity: (activity.local_date, activity.id))
    return activities


def _month_key(value: str) -> tuple[int, int]:
    return month_start(value).year, month_start(value).month


def _month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_start(month: str) -> date:
    """The first day of a YYYY-MM month; ValueError on anything else."""
    if not _MONTH_PATTERN.fullmatch(month):
        raise ValueError(f"Expected a month as YYYY-MM, got {month!r}")
    return date.fromisoformat(f"{month}-01")


def _first_day(month: str) -> date:
    return month_start(month)


def _last_day(month: str) -> date:
    first = _first_day(month)
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _shift_month(month: str, delta: int) -> str:
    year, number = _month_key(month)
    index = year * 12 + (number - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _months(start_month: str, end_month: str) -> list[str]:
    months = [start_month]
    while months[-1] != end_month:
        months.append(_shift_month(months[-1], 1))
    return months


def _money(value: float) -> float:
    return _round(value, _MONEY)


def _ratio(value: float | None) -> float | None:
    return None if value is None else _round(value, _RATIO)


def _round(value: float, quantum: Decimal) -> float:
    # repr() is the shortest string that round-trips the float, so the result
    # depends only on the value; + 0.0 folds -0.0 into 0.0.
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)) + 0.0


def _interest_month(activity: Activity, config: ReturnsConfig) -> str:
    month = _month_of(activity.local_date)
    if config.interest_attribution != "accrual":
        return month
    rule = config.interest_rules.get(activity.account_id, InterestRule())
    if rule.attribution == "previous_month":
        return _shift_month(month, -1)
    return month


def _twr(days: list[_Day], tolerance: float) -> tuple[float | None, list[str]]:
    """Chain daily returns; None if any day earned money on no capital."""
    growth = 1.0
    undefined = []
    for day in days:
        if day.capital <= _ZERO_CAPITAL:
            # Nothing invested and nothing earned is a flat day. Earning
            # something on nothing has no return - report it, don't guess.
            if abs(day.d_np) > tolerance:
                undefined.append(day.date.isoformat())
            continue
        growth *= 1.0 + day.d_np / day.capital
    if undefined:
        return None, undefined
    return growth - 1.0, undefined


def _dietz(
    days: list[_Day], start: date, end: date, opening_value: float
) -> tuple[float, float | None]:
    """Return (avg_invested_capital, modified_dietz) over [start, end]."""
    span = (end - start).days + 1
    weighted = math.fsum(
        (span - (day.date - start).days) / span * day.flow for day in days
    )
    capital = math.fsum([opening_value, weighted])
    pnl = math.fsum(day.d_np for day in days)
    if capital <= _ZERO_CAPITAL:
        return capital, None
    return capital, pnl / capital


def compute_monthly_returns(
    rows: list[ChartRow],
    activities: list[Activity],
    config: ReturnsConfig,
    start_month: str,
    end_month: str,
    last_complete_date: date | None = None,
) -> dict[str, Any]:
    """Compute per-month returns and a period summary.

    Args:
        rows: Parsed chart rows from a single performance response. Deltas are
            only ever taken between rows of this one list.
        activities: Parsed activities (all of them; filtering happens here).
        config: Calendars, interest attribution and invariant tolerance.
        start_month: First month, YYYY-MM.
        end_month: Last month, YYYY-MM.
        last_complete_date: Chart rows after this date are dropped - used to
            leave out a day whose prices are still moving. None keeps them all.

    Raises:
        ValueError: On malformed months, or when the chart has no row before
            ``start_month`` to measure the first day against.
    """
    if _month_key(start_month) > _month_key(end_month):
        raise ValueError("start_month must not be after end_month")
    tolerance = config.invariant_tolerance

    dropped = []
    if last_complete_date is not None:
        dropped = [r.date.isoformat() for r in rows if r.date > last_complete_date]
        rows = [r for r in rows if r.date <= last_complete_date]

    window_start = _first_day(start_month)
    requested_end = _last_day(end_month)
    baseline_index = bisect_left([r.date for r in rows], window_start) - 1
    if baseline_index < 0:
        raise ValueError(
            f"The chart has no row before {window_start.isoformat()}, so the "
            f"first day of {start_month} cannot be measured"
        )
    scope = [r for r in rows[baseline_index:] if r.date <= requested_end]
    baseline = scope[0]
    if len(scope) < 2:
        raise ValueError(f"The chart has no rows in {start_month}..{end_month}")
    last_date = scope[-1].date
    months = _months(start_month, min(end_month, _month_of(last_date)))

    days = [
        _Day(
            date=cur.date,
            prev_date=prev.date,
            prev_value=prev.value,
            d_np=cur.net_performance - prev.net_performance,
            d_value=cur.value - prev.value,
            d_investment=cur.total_investment - prev.total_investment,
        )
        for prev, cur in pairwise(scope)
    ]
    day_dates = [day.date for day in days]

    def chart_bucket(activity: Activity) -> date | None:
        """The chart day an activity lands in (the next row after a gap)."""
        if activity.chart_date <= baseline.date or activity.chart_date > last_date:
            return None
        return day_dates[bisect_left(day_dates, activity.chart_date)]

    # Activities stop at the last chart row: anything later is not reflected
    # in the chart this result is computed from.
    known = [
        a for a in activities if a.local_date <= last_date and a.chart_date <= last_date
    ]

    fees_by_day: dict[date, list[float]] = defaultdict(list)
    interest_by_day: dict[date, list[float]] = defaultdict(list)
    sells_by_day: set[date] = set()
    calendar_mismatches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in known:
        bucket = chart_bucket(activity)
        if bucket is None:
            continue
        if activity.fee:
            fees_by_day[bucket].append(activity.fee)
        if activity.type == "INTEREST":
            interest_by_day[bucket].append(activity.amount)
        if activity.type == "SELL":
            sells_by_day.add(bucket)
        if activity.type in ("BUY", "SELL") or activity.fee:
            local_month = _month_of(activity.local_date)
            chart_month = _month_of(bucket)
            if local_month != chart_month:
                calendar_mismatches[chart_month].append(
                    {
                        "activity_id": activity.id,
                        "type": activity.type,
                        "local_date": activity.local_date.isoformat(),
                        "chart_date": bucket.isoformat(),
                    }
                )

    breaks_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day in days:
        fees = math.fsum(fees_by_day.get(day.date, []))
        residual = math.fsum([day.d_np, -day.d_value, day.d_investment, fees])
        if abs(residual) <= tolerance:
            continue
        interest = math.fsum(interest_by_day.get(day.date, []))
        if day.date in sells_by_day:
            hint = "sell_day"
        elif interest and abs(residual - interest) <= tolerance:
            hint = "matches_interest"
        else:
            hint = "unexplained"
        breaks_by_month[_month_of(day.date)].append(
            {"date": day.date.isoformat(), "residual": _money(residual), "hint": hint}
        )

    gaps_by_month: dict[str, list[str]] = defaultdict(list)
    boundary_gaps: set[str] = set()
    for day in days:
        missing = day.prev_date + timedelta(days=1)
        if missing == day.date:
            continue
        if _month_of(missing) != _month_of(day.date):
            boundary_gaps.update({_month_of(missing), _month_of(day.date)})
        while missing < day.date:
            gaps_by_month[_month_of(missing)].append(missing.isoformat())
            missing += timedelta(days=1)

    interest_by_month: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    account_names: dict[str, str] = {}
    sell_days_by_month: dict[str, set[str]] = defaultdict(set)
    unsupported: dict[str, int] = defaultdict(int)
    for activity in known:
        local_month = _month_of(activity.local_date)
        if activity.type == "INTEREST":
            month = _interest_month(activity, config)
            if month in months:
                interest_by_month[month][activity.account_id].append(activity.amount)
                account_names[activity.account_id] = activity.account_name
        if local_month not in months:
            continue
        if activity.type == "SELL":
            sell_days_by_month[local_month].add(activity.local_date.isoformat())
        if activity.type not in SUPPORTED_ACTIVITY_TYPES:
            unsupported[activity.type] += 1

    expected_monthly = sorted(
        account_id
        for account_id, rule in config.interest_rules.items()
        if rule.expected == "monthly"
    )

    def interest_breakdown(per_account: Mapping[str, list[float]]) -> list[dict]:
        return [
            {
                "account_id": account_id,
                "account_name": account_names.get(account_id, ""),
                "interest": _money(math.fsum(per_account[account_id])),
            }
            for account_id in sorted(per_account)
        ]

    month_rows = []
    for month in months:
        month_days = [day for day in days if _month_of(day.date) == month]
        first, last = _first_day(month), _last_day(month)
        covered_end = min(last, last_date)
        market_pnl_net = math.fsum(day.d_np for day in month_days)
        fees = math.fsum(
            fee for day in month_days for fee in fees_by_day.get(day.date, [])
        )
        per_account = interest_by_month.get(month, {})
        interest = math.fsum(v for amounts in per_account.values() for v in amounts)
        opening = month_days[0].prev_value if month_days else 0.0
        avg_capital, dietz = _dietz(month_days, first, covered_end, opening)
        twr, undefined = _twr(month_days, tolerance)
        month_rows.append(
            {
                "month": month,
                "market_pnl_net": _money(market_pnl_net),
                "fees": _money(fees),
                "market_pnl_gross": _money(market_pnl_net + fees),
                "interest": _money(interest),
                "interest_by_account": interest_breakdown(per_account),
                "total_gross": _money(math.fsum([market_pnl_net, fees, interest])),
                "net_flows": _money(math.fsum(d.d_investment for d in month_days)),
                "avg_invested_capital": _money(avg_capital),
                "twr": _ratio(twr),
                "modified_dietz": _ratio(dietz),
                "flags": {
                    "partial_month": covered_end < last,
                    "sell_days": sorted(sell_days_by_month.get(month, set())),
                    "missing_interest_accounts": [
                        a for a in expected_monthly if a not in per_account
                    ],
                    "invariant_breaks": breaks_by_month.get(month, []),
                    "chart_gaps": gaps_by_month.get(month, []),
                    "boundary_gap": month in boundary_gaps,
                    "calendar_mismatches": calendar_mismatches.get(month, []),
                    "undefined_return_days": undefined,
                },
            }
        )

    period_days = [day for day in days if _month_of(day.date) in months]
    period_end = min(_last_day(months[-1]), last_date)
    all_interest: dict[str, list[float]] = defaultdict(list)
    for month in months:
        for account_id, amounts in interest_by_month.get(month, {}).items():
            all_interest[account_id].extend(amounts)
    period_net = math.fsum(day.d_np for day in period_days)
    period_fees = math.fsum(
        fee for day in period_days for fee in fees_by_day.get(day.date, [])
    )
    period_interest = math.fsum(v for amounts in all_interest.values() for v in amounts)
    period_capital, period_dietz = _dietz(
        period_days, window_start, period_end, baseline.value
    )
    period_twr, period_undefined = _twr(period_days, tolerance)

    return {
        "method_version": METHOD_VERSION,
        "config": {
            "timezone": config.timezone,
            "chart_timezone": config.chart_timezone,
            "interest_attribution": config.interest_attribution,
            "interest_rules": {
                account_id: {"attribution": rule.attribution, "expected": rule.expected}
                for account_id, rule in sorted(config.interest_rules.items())
            },
            "invariant_tolerance": tolerance,
        },
        "window": {
            "start_month": start_month,
            "end_month": months[-1],
            "requested_end_month": end_month,
            "baseline_date": baseline.date.isoformat(),
            "first_date": days[0].date.isoformat() if days else None,
            "last_date": last_date.isoformat(),
            "dropped_chart_dates": dropped,
        },
        "months": month_rows,
        "summary": {
            "market_pnl_net": _money(period_net),
            "fees": _money(period_fees),
            "market_pnl_gross": _money(period_net + period_fees),
            "interest": _money(period_interest),
            "interest_by_account": interest_breakdown(all_interest),
            "total_gross": _money(
                math.fsum([period_net, period_fees, period_interest])
            ),
            "net_flows": _money(math.fsum(d.d_investment for d in period_days)),
            "avg_invested_capital": _money(period_capital),
            "twr": _ratio(period_twr),
            "modified_dietz": _ratio(period_dietz),
            "flags": {
                "partial_period": period_end < _last_day(end_month),
                "invariant_break_count": sum(len(v) for v in breaks_by_month.values()),
                "unsupported_activities": dict(sorted(unsupported.items())),
                "undefined_return_days": period_undefined,
            },
        },
    }
