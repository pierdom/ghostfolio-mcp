"""Deterministic Modelo 720 inputs: foreign account balances and securities.

Pure: no network, no clock, no environment reads. Not tax advice: it computes
the figures the declaration asks about from Ghostfolio data, for the accounts
the caller marks as foreign; whether and what to declare is the caller's call.

Conventions (``METHOD_VERSION`` changes whenever one of these does):

- Only accounts with ``modelo720.foreign`` are included, every one of them,
  whether or not it is excluded from Ghostfolio's analysis.
- ``valuation_date`` is Dec 31 of ``year``, or ``as_of`` while the year is
  still running (``year_complete`` is then false and figures are a preview).
- Cash (``accounts`` category): the balance on the valuation date and the
  average daily balance over Q4 (Oct 1 to the valuation date), both from the
  balance history, where a balance holds until the next snapshot. Days not
  covered by any snapshot are reported, not filled in.
- Securities: per account, the quantity held on the valuation date (BUY minus
  SELL up to that local date) times the last stored close on or before it.
- A category crosses the threshold (50,000 by default) when its total does;
  for cash, the larger of the year-end and Q4-average totals counts.
  ``increase_over_previous`` compares that same figure with
  ``previous_declared``; more than 20,000 by default is flagged.
- Only the base currency is supported; other trades are refused.
"""

import math
from collections import defaultdict
from collections.abc import Collection
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import timedelta
from typing import Any

from ghostfolio_mcp.analytics.audit import AccountInfo
from ghostfolio_mcp.analytics.audit import BalanceSnapshot
from ghostfolio_mcp.analytics.common import Activity
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import money
from ghostfolio_mcp.analytics.common import price_on_or_before
from ghostfolio_mcp.analytics.common import quantity

METHOD_VERSION = "modelo720/1"

CATEGORIES = ("accounts", "securities")


@dataclass(frozen=True)
class Modelo720Config:
    timezone: str = "Europe/Madrid"
    base_currency: str = "EUR"
    foreign_accounts: Collection[str] = field(default_factory=frozenset)
    threshold: float = 50_000.0
    increase_threshold: float = 20_000.0
    stale_price_days: int = 5


def _balance_on(snapshots: list[BalanceSnapshot], day: date) -> BalanceSnapshot | None:
    found = None
    for snapshot in snapshots:
        if snapshot.date > day:
            break
        found = snapshot
    return found


def compute_modelo720_inputs(
    accounts: list[AccountInfo],
    snapshots: list[BalanceSnapshot],
    activities: list[Activity],
    prices: Mapping[str, list[PricePoint]],
    config: Modelo720Config,
    year: int,
    as_of: date,
    previous_declared: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Year-end cash, Q4 average cash and year-end securities per foreign account.

    Raises:
        ValueError: When the year has not started by ``as_of``, or a trade in
            a foreign account is not in the base currency.
    """
    year_end = date(year, 12, 31)
    if date(year, 1, 1) > as_of:
        raise ValueError(f"{year} has not started yet")
    valuation_date = min(year_end, as_of)
    q4_start = date(year, 10, 1)
    foreign = set(config.foreign_accounts)
    previous = dict(previous_declared or {})

    trades = [
        a
        for a in activities
        if a.type in ("BUY", "SELL")
        and a.account_id in foreign
        and a.local_date <= valuation_date
    ]
    other_currency = sorted(
        a.id for a in trades if a.currency and a.currency != config.base_currency
    )
    if other_currency:
        raise ValueError(
            f"{len(other_currency)} trade(s) in foreign accounts are not in "
            f"{config.base_currency} (first: {other_currency[0]}); currency "
            "conversion is not supported"
        )

    by_account_snapshots: dict[str, list[BalanceSnapshot]] = defaultdict(list)
    for snapshot in sorted(snapshots, key=lambda s: (s.account_id, s.date)):
        by_account_snapshots[snapshot.account_id].append(snapshot)
    holdings: dict[tuple[str, str], list[float]] = defaultdict(list)
    for trade in trades:
        signed = trade.quantity if trade.type == "BUY" else -trade.quantity
        holdings[(trade.account_id, trade.security)].append(signed)

    rows = []
    cash_year_end: list[float] = []
    cash_q4: list[float] = []
    securities_total: list[float] = []
    for account in sorted((a for a in accounts if a.id in foreign), key=lambda a: a.id):
        own = by_account_snapshots.get(account.id, [])
        closing = _balance_on(own, valuation_date)
        q4 = None
        q4_covered = 0
        q4_days = 0
        if valuation_date >= q4_start:
            q4_days = (valuation_date - q4_start).days + 1
            daily = []
            for i in range(q4_days):
                snapshot = _balance_on(own, q4_start + timedelta(days=i))
                if snapshot is not None:
                    daily.append(snapshot.value)
            q4_covered = len(daily)
            q4 = math.fsum(daily) / q4_covered if daily else None

        positions: list[dict[str, Any]] = []
        for (account_id, security), changes in sorted(holdings.items()):
            if account_id != account.id:
                continue
            held = math.fsum(changes)
            if held <= 1e-9:
                continue
            point = price_on_or_before(prices.get(security, []), valuation_date)
            value = held * point.price if point else None
            positions.append(
                {
                    "security": security,
                    "quantity": quantity(held),
                    "price": point.price if point else None,
                    "price_date": point.date.isoformat() if point else None,
                    "value": money(value) if value is not None else None,
                    "flags": {
                        "no_price": point is None,
                        "stale_price": point is not None
                        and (valuation_date - point.date).days
                        > config.stale_price_days,
                    },
                    "_value": value,
                }
            )

        account_securities = math.fsum(
            p["_value"] for p in positions if p["_value"] is not None
        )
        if closing is not None:
            cash_year_end.append(closing.value)
        if q4 is not None:
            cash_q4.append(q4)
        securities_total.append(account_securities)
        rows.append(
            {
                "account_id": account.id,
                "account_name": account.name,
                "excluded_from_analysis": account.excluded_from_analysis,
                "cash": {
                    "balance": money(closing.value) if closing else None,
                    "balance_snapshot_date": closing.date.isoformat()
                    if closing
                    else None,
                    "q4_average": money(q4) if q4 is not None else None,
                    "q4_days": q4_days,
                    "q4_covered_days": q4_covered,
                },
                "securities": {
                    "value": money(account_securities),
                    "positions": [
                        {k: v for k, v in p.items() if not k.startswith("_")}
                        for p in positions
                    ],
                },
                "flags": {
                    "no_balance_history": closing is None,
                    "q4_partially_covered": bool(q4_days) and q4_covered < q4_days,
                    "positions_without_price": sum(
                        1 for p in positions if p["_value"] is None
                    ),
                },
            }
        )

    year_end_cash = math.fsum(cash_year_end)
    q4_cash = math.fsum(cash_q4)
    measured = {
        "accounts": max(year_end_cash, q4_cash),
        "securities": math.fsum(securities_total),
    }

    def category(name: str, extra: dict[str, Any]) -> dict[str, Any]:
        value = measured[name]
        prior = previous.get(name)
        increase = value - prior if prior is not None else None
        return {
            **extra,
            "measured_value": money(value),
            "exceeds_threshold": value > config.threshold,
            "previous_declared": money(prior) if prior is not None else None,
            "increase_over_previous": money(increase) if increase is not None else None,
            "increase_exceeds_threshold": increase is not None
            and increase > config.increase_threshold,
        }

    return {
        "method_version": METHOD_VERSION,
        "config": {
            "timezone": config.timezone,
            "base_currency": config.base_currency,
            "foreign_accounts": sorted(foreign),
            "threshold": config.threshold,
            "increase_threshold": config.increase_threshold,
        },
        "year": year,
        "valuation_date": valuation_date.isoformat(),
        "year_complete": as_of >= year_end,
        "accounts": rows,
        "categories": {
            "accounts": category(
                "accounts",
                {
                    "year_end_balance": money(year_end_cash),
                    "q4_average_balance": money(q4_cash),
                },
            ),
            "securities": category(
                "securities", {"year_end_value": money(measured["securities"])}
            ),
        },
    }
