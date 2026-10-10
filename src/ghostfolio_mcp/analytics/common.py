"""Shared parsing, calendar and rounding helpers for the analytics modules.

Pure like the modules that use it: no network, no clock, no environment reads.
"""

import re
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import ROUND_HALF_EVEN
from decimal import Decimal
from itertools import pairwise
from typing import Any
from typing import Literal
from zoneinfo import ZoneInfo

_MONTH_PATTERN = re.compile(r"\d{4}-\d{2}")

_MONEY = Decimal("0.01")
_RATIO = Decimal("0.00000001")
_QUANTITY = Decimal("0.00000001")


@dataclass(frozen=True)
class ChartRow:
    """One daily row of the Ghostfolio v2 performance chart, in base currency."""

    date: date
    net_performance: float
    value: float
    total_investment: float
    cash: float = 0.0


@dataclass(frozen=True)
class Activity:
    """A Ghostfolio activity with its date resolved on both calendars.

    ``amount`` and ``fee`` are in the base currency; ``unit_price`` and
    ``native_fee`` are in ``currency``, the activity's own currency.
    """

    id: str
    type: str
    account_id: str
    account_name: str
    symbol: str
    local_date: date
    chart_date: date
    amount: float
    fee: float
    data_source: str = ""
    quantity: float = 0.0
    unit_price: float = 0.0
    currency: str = ""
    timestamp: str = ""
    native_fee: float = 0.0

    @property
    def security(self) -> str:
        """The identity of the asset, ``DATASOURCE:SYMBOL``."""
        return f"{self.data_source}:{self.symbol}"


@dataclass(frozen=True)
class InterestRule:
    """How one account pays interest.

    ``attribution="previous_month"`` moves a payment into the month before its
    payment date (an account that pays on the 1st for the month just ended).
    ``expected`` is the cadence the account is known to pay at: a month or a
    day without interest from it is then reported as missing.
    """

    attribution: Literal["payment_date", "previous_month"] = "payment_date"
    expected: Literal["none", "daily", "monthly"] = "none"


@dataclass(frozen=True)
class PricePoint:
    """One stored daily price of an asset."""

    date: date
    price: float
    carried_forward: bool = False


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
            cash=float(row.get("totalCashInBaseCurrency") or 0),
        )
        for row in raw_chart
    ]
    rows.sort(key=lambda row: row.date)
    for prev, cur in pairwise(rows):
        if prev.date == cur.date:
            raise ValueError(f"Duplicate chart date {cur.date.isoformat()}")
    return rows


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp that must carry a UTC offset."""
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise ValueError(f"Timestamp without a UTC offset: {value!r}")
    return timestamp


def parse_activities(
    raw_activities: Iterable[Mapping[str, Any]],
    timezone: str,
    chart_timezone: str,
) -> list[Activity]:
    """Parse ``activities`` of ``GET /api/v1/activities``.

    Each timestamp is converted to both ``timezone`` and ``chart_timezone``
    before its date is taken - never sliced or shifted by a fixed offset.
    Activities are returned sorted by local date, then id.
    """
    local_tz = ZoneInfo(timezone)
    chart_tz = ZoneInfo(chart_timezone)
    activities = []
    for raw in raw_activities:
        timestamp = parse_timestamp(raw["date"])
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
                data_source=str(profile.get("dataSource") or ""),
                quantity=float(raw.get("quantity") or 0),
                unit_price=float(raw.get("unitPrice") or 0),
                currency=str(raw.get("currency") or profile.get("currency") or ""),
                timestamp=str(raw["date"]),
                native_fee=float(raw.get("fee") or 0),
            )
        )
    activities.sort(key=lambda activity: (activity.local_date, activity.id))
    return activities


def parse_market_data(raw_points: Iterable[Mapping[str, Any]]) -> list[PricePoint]:
    """Parse ``marketData`` of ``GET /api/v1/asset/<dataSource>/<symbol>``.

    Ghostfolio stores one price per UTC day; rows are returned sorted by date.
    """
    points = {
        date.fromisoformat(str(raw["date"])[:10]): PricePoint(
            date=date.fromisoformat(str(raw["date"])[:10]),
            price=float(raw["marketPrice"]),
            carried_forward=bool(raw.get("isCarriedForward")),
        )
        for raw in raw_points
        if raw.get("marketPrice") is not None
    }
    return [points[day] for day in sorted(points)]


def price_on_or_before(points: list[PricePoint], day: date) -> PricePoint | None:
    """The last stored price on or before ``day``, or None."""
    found = None
    for point in points:
        if point.date > day:
            break
        found = point
    return found


def month_start(month: str) -> date:
    """The first day of a YYYY-MM month; ValueError on anything else."""
    if not _MONTH_PATTERN.fullmatch(month):
        raise ValueError(f"Expected a month as YYYY-MM, got {month!r}")
    return date.fromisoformat(f"{month}-01")


def month_end(month: str) -> date:
    """The last day of a YYYY-MM month."""
    first = month_start(month)
    following = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return following - timedelta(days=1)


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def month_key(month: str) -> tuple[int, int]:
    first = month_start(month)
    return first.year, first.month


def shift_month(month: str, delta: int) -> str:
    year, number = month_key(month)
    index = year * 12 + (number - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def months_between(start_month: str, end_month: str) -> list[str]:
    """Every month from start to end inclusive; ValueError if reversed."""
    if month_key(start_month) > month_key(end_month):
        raise ValueError("start_month must not be after end_month")
    months = [start_month]
    while months[-1] != end_month:
        months.append(shift_month(months[-1], 1))
    return months


def money(value: float) -> float:
    """Round a base-currency amount for output (2 decimals, half-even)."""
    return _round(value, _MONEY)


def ratio(value: float | None) -> float | None:
    """Round a return or rate for output (8 decimals, half-even)."""
    return None if value is None else _round(value, _RATIO)


def quantity(value: float) -> float:
    """Round a share quantity for output (8 decimals, half-even)."""
    return _round(value, _QUANTITY)


def _round(value: float, quantum: Decimal) -> float:
    # repr() is the shortest string that round-trips the float, so the result
    # depends only on the value; + 0.0 folds -0.0 into 0.0.
    return float(Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)) + 0.0
