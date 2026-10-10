"""Deterministic effective cash yield per account from balance history.

Pure: no network, no clock, no environment reads.

Conventions (``METHOD_VERSION`` changes whenever one of these does):

- An account's balance on day d is the value of its latest balance snapshot
  dated on or before d: snapshots are step changes, the balance holds until the
  next one. Days before the first snapshot are not covered.
- ``avg_balance`` is the mean of that daily balance over the covered days of
  the period.
- Interest is attributed to months as in ``get_monthly_returns``: by local
  payment date, or under ``accrual`` with each account's
  ``InterestRule.attribution``.
- ``interest`` is gross; ``interest_fees`` are the fees recorded on the
  interest activities (typically withholding tax). ``net_*`` yields use
  ``interest - interest_fees``.
- ``period_yield = interest / avg_balance`` and
  ``annualized_yield = period_yield * 365 / days`` (simple, ACT/365), where
  ``days`` is the number of days in the period up to ``as_of``. Both are only
  given when every one of those days is covered by a snapshot - a partly
  covered period would set a whole month's interest against part of its
  balance.
- The portfolio ``total`` uses the same formulas on the sums over the accounts
  covered on every day of the period; the others are named in
  ``accounts_left_out``.
"""

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import timedelta
from itertools import pairwise
from typing import Any
from typing import Literal

from ghostfolio_mcp.analytics.audit import AccountInfo
from ghostfolio_mcp.analytics.audit import BalanceSnapshot
from ghostfolio_mcp.analytics.common import Activity
from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import money
from ghostfolio_mcp.analytics.common import month_end
from ghostfolio_mcp.analytics.common import month_of
from ghostfolio_mcp.analytics.common import month_start
from ghostfolio_mcp.analytics.common import months_between
from ghostfolio_mcp.analytics.common import ratio
from ghostfolio_mcp.analytics.common import shift_month

METHOD_VERSION = "cash-yield/1"

DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class CashYieldConfig:
    timezone: str = "Europe/Madrid"
    interest_attribution: Literal["payment_date", "accrual"] = "payment_date"
    interest_rules: Mapping[str, InterestRule] = field(default_factory=dict)
    stale_snapshot_days: int = 45


def _attributed_month(activity: Activity, config: CashYieldConfig) -> str:
    month = month_of(activity.local_date)
    if config.interest_attribution != "accrual":
        return month
    rule = config.interest_rules.get(activity.account_id, InterestRule())
    return shift_month(month, -1) if rule.attribution == "previous_month" else month


def _daily_balances(
    snapshots: list[BalanceSnapshot], start: date, end: date
) -> dict[date, float]:
    """Balance on each covered day of [start, end] (step function)."""
    ordered = sorted(snapshots, key=lambda s: s.date)
    balances = {}
    index, current = 0, None
    day = start
    # Advance to the last snapshot on or before the first day.
    while index < len(ordered) and ordered[index].date <= start:
        current = ordered[index].value
        index += 1
    while day <= end:
        while index < len(ordered) and ordered[index].date <= day:
            current = ordered[index].value
            index += 1
        if current is not None:
            balances[day] = current
        day += timedelta(days=1)
    return balances


def _yields(
    interest: float, interest_fees: float, avg: float | None, days: int
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """Gross and net (after interest fees) period and annualized yields."""
    if avg is None or avg <= 0:
        return (
            {"period_yield": None, "annualized_yield": None},
            {"net_period_yield": None, "net_annualized_yield": None},
        )
    gross = interest / avg
    net = (interest - interest_fees) / avg
    return (
        {
            "period_yield": ratio(gross),
            "annualized_yield": ratio(gross * DAYS_PER_YEAR / days),
        },
        {
            "net_period_yield": ratio(net),
            "net_annualized_yield": ratio(net * DAYS_PER_YEAR / days),
        },
    )


def _measure(
    balances: dict[date, float],
    interest: float,
    interest_fees: float,
    start: date,
    end: date,
) -> dict[str, Any]:
    days = (end - start).days + 1
    covered = len(balances)
    avg = math.fsum(balances.values()) / covered if covered else None
    complete = covered == days
    gross, net = _yields(interest, interest_fees, avg if complete else None, days)
    return {
        "days": days,
        "covered_days": covered,
        "avg_balance": money(avg) if avg is not None else None,
        "interest": money(interest),
        "interest_fees": money(interest_fees),
        **gross,
        **net,
        "_avg": avg,
        "_complete": complete,
    }


def compute_cash_yield(
    accounts: list[AccountInfo],
    snapshots: list[BalanceSnapshot],
    activities: list[Activity],
    config: CashYieldConfig,
    start_month: str,
    end_month: str,
    as_of: date,
) -> dict[str, Any]:
    """Per-account and portfolio cash yield for each month and the period.

    Args:
        accounts: Accounts to report (all of them, excluded-from-analysis too).
        snapshots: Balance history entries of all accounts.
        activities: Parsed activities; only INTEREST ones are used.
        config: Timezone, interest attribution and rules.
        start_month: First month, YYYY-MM.
        end_month: Last month, YYYY-MM; clipped to the month of ``as_of``.
        as_of: The last complete day.

    Raises:
        ValueError: On malformed months, or a start after ``as_of``.
    """
    window_start = month_start(start_month)
    if window_start > as_of:
        raise ValueError(f"{start_month} starts after {as_of.isoformat()}")
    months = months_between(start_month, min(end_month, month_of(as_of)))
    window_end = min(month_end(months[-1]), as_of)

    snapshots_by_account: dict[str, list[BalanceSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        snapshots_by_account[snapshot.account_id].append(snapshot)

    interest: dict[tuple[str, str], list[float]] = defaultdict(list)
    interest_fees: dict[tuple[str, str], list[float]] = defaultdict(list)
    for activity in activities:
        if activity.type != "INTEREST" or activity.local_date > as_of:
            continue
        month = _attributed_month(activity, config)
        if month in months:
            interest[(activity.account_id, month)].append(activity.amount)
            interest_fees[(activity.account_id, month)].append(activity.fee)

    ordered_accounts = sorted(accounts, key=lambda a: a.id)

    def flags_for(account: AccountInfo, start: date, end: date, measured) -> dict:
        own = sorted(snapshots_by_account.get(account.id, []), key=lambda s: s.date)
        first = own[0].date if own else None
        last_before = [s.date for s in own if s.date <= end]
        last = max(last_before) if last_before else None
        # Snapshots that set a balance inside the period: the last one on or
        # before its start, then every one up to its end.
        opening = [s.date for s in own if s.date <= start][-1:]
        inside = opening + [s.date for s in own if start < s.date <= end]
        gaps = [(b - a).days for a, b in pairwise(inside)]
        return {
            "history_starts": first.isoformat() if first else None,
            "partial_coverage": not measured["_complete"],
            "stale_snapshot": last is not None
            and (end - last).days > config.stale_snapshot_days,
            "max_snapshot_gap_days": max(gaps, default=None),
            "interest_without_balance": measured["interest"] > 0
            and not (measured["_avg"] or 0) > 0,
        }

    def report(start: date, end: date, period_months: list[str]) -> dict[str, Any]:
        rows = []
        total_interest: list[float] = []
        total_avg: list[float] = []
        total_fees: list[float] = []
        # Accounts not covered on every day of the period would set a whole
        # period's interest against part of its balance; they are listed
        # instead of being summed.
        left_out: list[str] = []
        for account in ordered_accounts:
            balances = _daily_balances(
                snapshots_by_account.get(account.id, []), start, end
            )
            paid = math.fsum(
                v for m in period_months for v in interest.get((account.id, m), [])
            )
            withheld = math.fsum(
                v for m in period_months for v in interest_fees.get((account.id, m), [])
            )
            if not balances and not paid:
                continue
            measured = _measure(balances, paid, withheld, start, end)
            rows.append(
                {
                    "account_id": account.id,
                    "account_name": account.name,
                    "excluded_from_analysis": account.excluded_from_analysis,
                    **{k: v for k, v in measured.items() if not k.startswith("_")},
                    "flags": flags_for(account, start, end, measured),
                }
            )
            if measured["_complete"]:
                total_interest.append(paid)
                total_fees.append(withheld)
                total_avg.append(measured["_avg"])
            else:
                left_out.append(account.id)
        days = (end - start).days + 1
        avg = math.fsum(total_avg)
        paid = math.fsum(total_interest)
        withheld = math.fsum(total_fees)
        gross, net = _yields(paid, withheld, avg, days)
        return {
            "accounts": rows,
            "total": {
                "days": days,
                "avg_balance": money(avg),
                "interest": money(paid),
                "interest_fees": money(withheld),
                **gross,
                **net,
                "accounts_left_out": left_out,
            },
        }

    month_rows = []
    for month in months:
        start = month_start(month)
        end = min(month_end(month), as_of)
        month_rows.append(
            {
                "month": month,
                "partial_month": end < month_end(month),
                **report(start, end, [month]),
            }
        )

    return {
        "method_version": METHOD_VERSION,
        "config": {
            "timezone": config.timezone,
            "interest_attribution": config.interest_attribution,
            "interest_rules": {
                account_id: {"attribution": rule.attribution, "expected": rule.expected}
                for account_id, rule in sorted(config.interest_rules.items())
            },
            "stale_snapshot_days": config.stale_snapshot_days,
            "days_per_year": DAYS_PER_YEAR,
        },
        "window": {
            "start_month": start_month,
            "end_month": months[-1],
            "requested_end_month": end_month,
            "as_of": as_of.isoformat(),
        },
        "months": month_rows,
        "summary": report(window_start, window_end, months),
    }
