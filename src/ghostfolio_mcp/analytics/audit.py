"""Deterministic data-quality audit of a Ghostfolio portfolio.

Pure: the caller fetches activities, accounts, balance history, stored prices
and the daily performance chart, and hands them in parsed. Every finding names
the check that produced it, a severity, the activities and dates involved and
the evidence; nothing is corrected. The same inputs always give byte-identical
output.

Checks (``METHOD_VERSION`` changes whenever one is added or its rule changes):

- ``trade_date_mismatch`` - an activity timestamped between local and UTC
  midnight has two candidate dates: Ghostfolio books it on the UTC date, while
  the local date is what a person entering "the 23rd" meant. The stored price
  decides: a candidate is a trading day when its stored close is not carried
  forward nor a repeat of the previous day's close (Ghostfolio fills weekends
  with the Friday close), and the price is attributed to the trading day
  whose close it matches or is meaningfully nearer to. Trades of one account
  sharing a timestamp (an order batch) get one verdict, from their mean
  distance to each close. ``booked_day_before_trade`` (warn) when the price
  belongs to the local date, so Ghostfolio values the trade a day early;
  ``local_date_after_trade`` (info) when it belongs to the UTC date, so the
  local date - used for monthly bucketing - is a day late.
- ``price_far_from_close`` (warn) - the price is further than
  ``price_far_ratio`` from the close on the date Ghostfolio books it.
- ``duplicate_activity`` (error) - same account, type, asset, local date,
  quantity, price and fee.
- ``interest_day_conflict`` (warn) - a daily interest payer with more than one
  payment of the same kind on one local day.
- ``missing_interest_day`` / ``missing_interest_month`` (warn) - a daily or
  monthly payer (``InterestRule.expected``) with a day or month without
  interest between its first payment and ``as_of``. Daily cadence is judged
  per kind of interest (asset): only a kind paid on at least half the days of
  its span counts as daily, so occasional lending income is not checked.
- ``cash_without_interest`` (info) - an account holding cash that paid no
  interest within ``interest_window_days``. Accounts in
  ``no_interest_accounts`` (configured as paying no interest) are skipped.
- ``stale_balance`` (info) - an account with a cash balance whose last
  balance snapshot is older than ``stale_balance_days``.
- ``stale_price`` (warn) - a held asset whose last real close (not carried
  forward, not a repeat of the day before) is older than ``stale_price_days``.
- ``chart_gap`` (info) and ``invariant_break`` (warn) - missing chart days and
  days where ``dNP - (dV - dInvestment) + fees`` is not zero outside sell days,
  as computed by :mod:`monthly_returns`.
- ``legacy_seed_position`` (info) - the first buy precedes the next one by more
  than ``seed_gap_days`` and is below ``seed_share`` of all buys; it stretches
  the chart back and distorts percentage returns.
- ``mixed_timestamp_styles`` (info) - one account records the same kind of
  activity at UTC midnight, local midnight and/or intraday times.
- ``missing_isin`` (info) - assets without an ISIN.
- ``unsupported_activity_type`` / ``non_base_currency`` (info).
"""

import math
from collections import Counter
from collections import defaultdict
from collections.abc import Collection
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import datetime
from datetime import time
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ghostfolio_mcp.analytics.common import Activity
from ghostfolio_mcp.analytics.common import ChartRow
from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import money
from ghostfolio_mcp.analytics.common import month_of
from ghostfolio_mcp.analytics.common import months_between
from ghostfolio_mcp.analytics.common import parse_timestamp
from ghostfolio_mcp.analytics.common import quantity
from ghostfolio_mcp.analytics.common import ratio
from ghostfolio_mcp.analytics.common import shift_month
from ghostfolio_mcp.analytics.monthly_returns import ReturnsConfig
from ghostfolio_mcp.analytics.monthly_returns import compute_monthly_returns

METHOD_VERSION = "audit/2"

SUPPORTED_ACTIVITY_TYPES = frozenset({"BUY", "SELL", "INTEREST"})

_SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}

# A kind of interest is only judged daily over at least this many days.
DAILY_MIN_SPAN_DAYS = 7


@dataclass(frozen=True)
class AccountInfo:
    id: str
    name: str
    balance: float
    excluded_from_analysis: bool = False


@dataclass(frozen=True)
class BalanceSnapshot:
    account_id: str
    date: date
    value: float


@dataclass(frozen=True)
class AuditConfig:
    timezone: str = "Europe/Madrid"
    chart_timezone: str = "UTC"
    base_currency: str = "EUR"
    interest_rules: Mapping[str, InterestRule] = field(default_factory=dict)
    # Accounts explicitly configured as paying no interest.
    no_interest_accounts: Collection[str] = field(default_factory=frozenset)
    # A price nearer one candidate date's close than the other's by at least
    # this share of the close decides the trade date.
    trade_date_min_gap: float = 0.002
    # A mean distance this small means the price is that day's close.
    trade_date_exact_gap: float = 0.0005
    price_far_ratio: float = 0.05
    interest_window_days: int = 190
    cash_threshold: float = 100.0
    stale_balance_days: int = 45
    stale_price_days: int = 5
    seed_gap_days: int = 180
    seed_share: float = 0.01
    invariant_tolerance: float = 0.01


def _finding(
    check: str,
    severity: str,
    message: str,
    *,
    dates: list[date] | None = None,
    activities: list[Activity] | None = None,
    account: tuple[str, str] | None = None,
    security: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check": check,
        "severity": severity,
        "message": message,
        "account_id": account[0] if account else None,
        "account_name": account[1] if account else None,
        "security": security,
        "dates": sorted({d.isoformat() for d in dates or []}),
        "activity_ids": sorted(a.id for a in activities or []),
        "evidence": evidence or {},
    }


def _style(activity: Activity, local_tz: ZoneInfo) -> str:
    timestamp = parse_timestamp(activity.timestamp)
    if timestamp.astimezone(local_tz).time() == time(0, 0):
        return "local_midnight"
    if timestamp.astimezone(ZoneInfo("UTC")).time() == time(0, 0):
        return "utc_midnight"
    return "intraday"


def _relative_gap(price: float, close: float) -> float:
    return abs(price - close) / close


def _is_trading_day(points: Mapping[date, PricePoint], day: date) -> bool:
    """Whether the stored close for ``day`` is a real close.

    Ghostfolio stores a price for every calendar day and does not always mark
    weekend and holiday prices as carried forward, so a close identical to the
    previous day's close also counts as no trading.
    """
    point = points.get(day)
    if point is None or point.carried_forward:
        return False
    previous = points.get(day - timedelta(days=1))
    return previous is None or previous.price != point.price


def _check_far_prices(
    activities: list[Activity],
    prices: Mapping[str, list[PricePoint]],
    config: AuditConfig,
) -> list[dict[str, Any]]:
    findings = []
    for activity in activities:
        if activity.type not in ("BUY", "SELL") or activity.unit_price <= 0:
            continue
        points = {p.date: p for p in prices.get(activity.security, [])}
        if not _is_trading_day(points, activity.chart_date):
            continue
        close = points[activity.chart_date].price
        gap = _relative_gap(activity.unit_price, close)
        if gap > config.price_far_ratio:
            findings.append(
                _finding(
                    "price_far_from_close",
                    "warn",
                    f"{activity.type} price is {gap:.1%} away from the close on the "
                    "day Ghostfolio books it",
                    dates=[activity.chart_date],
                    activities=[activity],
                    account=(activity.account_id, activity.account_name),
                    security=activity.security,
                    evidence={"unit_price": activity.unit_price, "close": close},
                )
            )
    return findings


def _check_trade_dates(
    activities: list[Activity],
    prices: Mapping[str, list[PricePoint]],
    config: AuditConfig,
) -> list[dict[str, Any]]:
    """Decide the trade date of local-midnight trades, one verdict per batch.

    Trades of one account sharing a timestamp were entered together (an
    order batch, a savings-plan run), so they are judged together: a
    candidate date counts only if it is a trading day for every member, and
    with two candidates the members' mean distance to each close decides.
    """
    groups: dict[tuple[str, Any], list[Activity]] = defaultdict(list)
    for activity in activities:
        if (
            activity.type in ("BUY", "SELL")
            and activity.unit_price > 0
            and activity.local_date != activity.chart_date
            and prices.get(activity.security)
        ):
            key = (activity.account_id, parse_timestamp(activity.timestamp))
            groups[key].append(activity)

    findings = []
    for (_account_id, _timestamp), members in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        members.sort(key=lambda a: a.id)
        first = members[0]
        candidates = {"local": first.local_date, "booked": first.chart_date}
        rows = []
        trading = {"local": True, "booked": True}
        gaps: dict[str, list[float]] = {"local": [], "booked": []}
        for member in members:
            points = {p.date: p for p in prices[member.security]}
            row: dict[str, Any] = {
                "activity_id": member.id,
                "security": member.security,
                "unit_price": member.unit_price,
            }
            for label, day in candidates.items():
                point = points.get(day)
                row[f"{label}_close"] = point.price if point else None
                if point is not None and _is_trading_day(points, day):
                    gaps[label].append(_relative_gap(member.unit_price, point.price))
                else:
                    trading[label] = False
            rows.append(row)
        usable = [label for label in ("local", "booked") if trading[label]]
        evidence: dict[str, Any] = {
            "local_date": first.local_date.isoformat(),
            "booked_date": first.chart_date.isoformat(),
            "trading_days": usable,
            "members": rows,
        }
        if not usable:
            continue
        if len(usable) == 1:
            verdict_label = usable[0]
            reason = "the only trading day of the two"
        else:
            mean = {
                label: math.fsum(gaps[label]) / len(gaps[label]) for label in usable
            }
            evidence["mean_gap"] = {label: ratio(mean[label]) for label in usable}
            nearer = min(usable, key=lambda label: (mean[label], label))
            other = "booked" if nearer == "local" else "local"
            if mean[nearer] <= config.trade_date_exact_gap < mean[other]:
                verdict_label = nearer
                reason = "the price is this day's close"
            elif mean[other] - mean[nearer] >= config.trade_date_min_gap:
                verdict_label = nearer
                reason = "the price is nearer this day's close"
            else:
                verdict_label = None
                reason = ""
        securities = sorted({m.security for m in members})
        common = {
            "dates": [first.local_date, first.chart_date],
            "activities": members,
            "account": (first.account_id, first.account_name),
            "security": securities[0] if len(securities) == 1 else None,
        }
        batch = f" ({len(members)} trades entered together)" if len(members) > 1 else ""
        if verdict_label is None:
            findings.append(
                _finding(
                    "trade_date_mismatch",
                    "info",
                    "Timestamp falls between local and UTC midnight and the price "
                    f"does not tell which day the trade was{batch}",
                    evidence={**evidence, "verdict": "ambiguous"},
                    **common,
                )
            )
        elif verdict_label == "local":
            findings.append(
                _finding(
                    "trade_date_mismatch",
                    "warn",
                    f"Traded on {first.local_date.isoformat()} ({reason}) but "
                    f"Ghostfolio books it on {first.chart_date.isoformat()}, a day "
                    f"early, because the timestamp is local midnight{batch}",
                    evidence={**evidence, "verdict": "booked_day_before_trade"},
                    **common,
                )
            )
        else:
            findings.append(
                _finding(
                    "trade_date_mismatch",
                    "info",
                    f"Traded on {first.chart_date.isoformat()} ({reason}), as "
                    "Ghostfolio books it, but its local date is "
                    f"{first.local_date.isoformat()}, a day late{batch}",
                    evidence={**evidence, "verdict": "local_date_after_trade"},
                    **common,
                )
            )
    return findings


def _check_duplicates(activities: list[Activity]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[Activity]] = defaultdict(list)
    for activity in activities:
        key = (
            activity.account_id,
            activity.type,
            activity.security,
            activity.local_date,
            activity.quantity,
            activity.unit_price,
            activity.fee,
        )
        groups[key].append(activity)
    return [
        _finding(
            "duplicate_activity",
            "error",
            f"{len(group)} identical {group[0].type} activities",
            dates=[group[0].local_date],
            activities=group,
            account=(group[0].account_id, group[0].account_name),
            security=group[0].security,
            evidence={"quantity": group[0].quantity, "unit_price": group[0].unit_price},
        )
        for group in groups.values()
        if len(group) > 1
    ]


def _check_daily_interest(
    paid: list[Activity], account: tuple[str, str], as_of: date
) -> list[dict[str, Any]]:
    """Conflicts and gaps for each kind of interest the account pays daily.

    An account can pay more than one kind of interest (cash interest daily,
    lending income now and then), so cadence is checked per asset. A kind is
    daily when it was paid on at least half of the days from its first to its
    last payment.
    """
    findings = []
    by_security: dict[str, list[Activity]] = defaultdict(list)
    for activity in paid:
        by_security[activity.security].append(activity)
    for security, payments in sorted(by_security.items()):
        paid_days = {a.local_date for a in payments}
        first, last = min(paid_days), max(paid_days)
        span = (last - first).days + 1
        if span < DAILY_MIN_SPAN_DAYS or len(paid_days) * 2 < span:
            continue
        per_day: dict[date, list[Activity]] = defaultdict(list)
        for activity in payments:
            per_day[activity.local_date].append(activity)
        for day, group in sorted(per_day.items()):
            if len(group) < 2:
                continue
            neighbours = [
                d
                for d in (day - timedelta(days=1), day + timedelta(days=1))
                if d not in paid_days and first <= d <= as_of
            ]
            findings.append(
                _finding(
                    "interest_day_conflict",
                    "warn",
                    f"{len(group)} payments on one day from a daily payer"
                    + (
                        f"; {neighbours[0].isoformat()} next to it has none, so "
                        "one is probably misdated"
                        if neighbours
                        else ""
                    ),
                    dates=[day, *neighbours],
                    activities=group,
                    account=account,
                    security=security,
                    evidence={"amounts": [money(a.amount) for a in group]},
                )
            )
        missing = [
            first + timedelta(days=i)
            for i in range((as_of - first).days + 1)
            if first + timedelta(days=i) not in paid_days
        ]
        if missing:
            findings.append(
                _finding(
                    "missing_interest_day",
                    "warn",
                    f"{len(missing)} day(s) without this daily payment",
                    dates=missing,
                    account=account,
                    security=security,
                )
            )
    return findings


def _check_interest(
    activities: list[Activity],
    accounts: Mapping[str, AccountInfo],
    config: AuditConfig,
    as_of: date,
) -> list[dict[str, Any]]:
    findings = []
    interest_by_account: dict[str, list[Activity]] = defaultdict(list)
    for activity in activities:
        if activity.type == "INTEREST" and activity.local_date <= as_of:
            interest_by_account[activity.account_id].append(activity)

    for account_id, rule in sorted(config.interest_rules.items()):
        paid = interest_by_account.get(account_id, [])
        if not paid:
            continue
        name = paid[0].account_name
        if rule.expected == "daily":
            findings.extend(_check_daily_interest(paid, (account_id, name), as_of))
        elif rule.expected == "monthly":
            paid_months = {month_of(a.local_date) for a in paid}
            last_complete = shift_month(month_of(as_of), -1)
            first_month = min(paid_months)
            if first_month <= last_complete:
                missing_months = [
                    m
                    for m in months_between(first_month, last_complete)
                    if m not in paid_months
                ]
                if missing_months:
                    findings.append(
                        _finding(
                            "missing_interest_month",
                            "warn",
                            f"No interest paid in {', '.join(missing_months)}",
                            account=(account_id, name),
                            evidence={"months": missing_months},
                        )
                    )

    window_start = as_of - timedelta(days=config.interest_window_days)
    for account in sorted(accounts.values(), key=lambda a: a.id):
        if (
            account.balance <= config.cash_threshold
            or account.id in config.no_interest_accounts
        ):
            continue
        recent = [
            a
            for a in interest_by_account.get(account.id, [])
            if a.local_date > window_start
        ]
        if recent:
            continue
        last = max(
            (a.local_date for a in interest_by_account.get(account.id, [])),
            default=None,
        )
        findings.append(
            _finding(
                "cash_without_interest",
                "info",
                f"Holds {money(account.balance)} in cash but recorded no interest in "
                f"the last {config.interest_window_days} days",
                dates=[last] if last else [],
                account=(account.id, account.name),
                evidence={
                    "balance": money(account.balance),
                    "last_interest_date": last.isoformat() if last else None,
                    "excluded_from_analysis": account.excluded_from_analysis,
                },
            )
        )
    return findings


def _check_balances(
    accounts: Mapping[str, AccountInfo],
    snapshots: list[BalanceSnapshot],
    config: AuditConfig,
    as_of: date,
) -> list[dict[str, Any]]:
    last: dict[str, date] = {}
    for snapshot in snapshots:
        if snapshot.date <= as_of:
            last[snapshot.account_id] = max(
                snapshot.date, last.get(snapshot.account_id, snapshot.date)
            )
    findings = []
    for account in sorted(accounts.values(), key=lambda a: a.id):
        if account.balance <= config.cash_threshold:
            continue
        latest = last.get(account.id)
        age = (as_of - latest).days if latest else None
        if age is not None and age <= config.stale_balance_days:
            continue
        findings.append(
            _finding(
                "stale_balance",
                "info",
                "No balance snapshot recorded"
                if latest is None
                else f"Last balance snapshot is {age} days old",
                dates=[latest] if latest else [],
                account=(account.id, account.name),
                evidence={
                    "balance": money(account.balance),
                    "last_snapshot": latest.isoformat() if latest else None,
                },
            )
        )
    return findings


def _check_prices(
    activities: list[Activity],
    prices: Mapping[str, list[PricePoint]],
    config: AuditConfig,
    as_of: date,
) -> list[dict[str, Any]]:
    held: dict[str, list[float]] = defaultdict(list)
    for activity in activities:
        if activity.type == "BUY":
            held[activity.security].append(activity.quantity)
        elif activity.type == "SELL":
            held[activity.security].append(-activity.quantity)
    findings = []
    for security in sorted(held):
        position = math.fsum(held[security])
        if position <= 1e-9:
            continue
        points = {p.date: p for p in prices.get(security, [])}
        real = [day for day in points if _is_trading_day(points, day)]
        latest = max(real, default=None)
        age = (as_of - latest).days if latest else None
        if age is not None and age <= config.stale_price_days:
            continue
        findings.append(
            _finding(
                "stale_price",
                "warn",
                "No stored price for a held asset"
                if latest is None
                else f"Last real close is {age} days old",
                dates=[latest] if latest else [],
                security=security,
                evidence={
                    "quantity": quantity(position),
                    "last_close_date": latest.isoformat() if latest else None,
                },
            )
        )
    return findings


def _check_chart(
    rows: list[ChartRow],
    activities: list[Activity],
    config: AuditConfig,
) -> list[dict[str, Any]]:
    if len(rows) < 2:
        return []
    start_month = shift_month(month_of(rows[0].date), 1)
    end_month = month_of(rows[-1].date)
    if start_month > end_month:
        return []
    result = compute_monthly_returns(
        rows,
        activities,
        ReturnsConfig(
            timezone=config.timezone,
            chart_timezone=config.chart_timezone,
            invariant_tolerance=config.invariant_tolerance,
        ),
        start_month,
        end_month,
    )
    gaps: list[date] = []
    findings = []
    for month in result["months"]:
        gaps.extend(date.fromisoformat(d) for d in month["flags"]["chart_gaps"])
        for item in month["flags"]["invariant_breaks"]:
            if item["hint"] == "sell_day":
                continue
            findings.append(
                _finding(
                    "invariant_break",
                    "warn",
                    "netPerformance moved by "
                    f"{item['residual']} more than value minus investment and fees"
                    + (
                        " - equal to that day's interest, so Ghostfolio may now "
                        "count interest in netPerformance"
                        if item["hint"] == "matches_interest"
                        else ""
                    ),
                    dates=[date.fromisoformat(item["date"])],
                    evidence={"residual": item["residual"], "hint": item["hint"]},
                )
            )
    if gaps:
        findings.append(
            _finding(
                "chart_gap",
                "info",
                f"{len(gaps)} day(s) missing from the daily performance chart",
                dates=gaps,
            )
        )
    return findings


def _check_seed(
    activities: list[Activity], config: AuditConfig
) -> list[dict[str, Any]]:
    buys = [a for a in activities if a.type == "BUY"]
    if len(buys) < 2:
        return []
    buys.sort(key=lambda a: (a.local_date, a.id))
    first, second = buys[0], buys[1]
    total = math.fsum(a.amount for a in buys)
    gap = (second.local_date - first.local_date).days
    share = first.amount / total if total else 0.0
    if gap <= config.seed_gap_days or share >= config.seed_share:
        return []
    return [
        _finding(
            "legacy_seed_position",
            "info",
            f"The first buy precedes the next by {gap} days and is "
            f"{share:.2%} of all buys; it stretches the chart back and distorts "
            "percentage returns",
            dates=[first.local_date, second.local_date],
            activities=[first],
            account=(first.account_id, first.account_name),
            security=first.security,
            evidence={"amount": money(first.amount), "share_of_buys": ratio(share)},
        )
    ]


def _check_styles(
    activities: list[Activity], config: AuditConfig
) -> list[dict[str, Any]]:
    local_tz = ZoneInfo(config.timezone)
    styles: dict[tuple[str, str], Counter] = defaultdict(Counter)
    names: dict[str, str] = {}
    for activity in activities:
        kind = "INTEREST" if activity.type == "INTEREST" else "TRADE"
        styles[(activity.account_id, kind)][_style(activity, local_tz)] += 1
        names[activity.account_id] = activity.account_name
    return [
        _finding(
            "mixed_timestamp_styles",
            "info",
            f"{kind.lower()} activities use {len(counts)} timestamp styles; "
            "local-midnight ones are booked by Ghostfolio on the previous UTC day",
            account=(account_id, names[account_id]),
            evidence={"kind": kind, "styles": dict(sorted(counts.items()))},
        )
        for (account_id, kind), counts in sorted(styles.items())
        if len(counts) > 1
    ]


def _check_misc(
    activities: list[Activity],
    isins: Mapping[str, str | None],
    config: AuditConfig,
) -> list[dict[str, Any]]:
    findings = []
    traded = sorted({a.security for a in activities if a.type in ("BUY", "SELL")})
    without_isin = [s for s in traded if not isins.get(s)]
    if without_isin:
        findings.append(
            _finding(
                "missing_isin",
                "info",
                f"{len(without_isin)} traded asset(s) have no ISIN, so the same "
                "security listed under two symbols cannot be matched",
                evidence={"securities": without_isin},
            )
        )
    unsupported = Counter(
        a.type for a in activities if a.type not in SUPPORTED_ACTIVITY_TYPES
    )
    if unsupported:
        findings.append(
            _finding(
                "unsupported_activity_type",
                "info",
                "Activity types the analytics tools do not interpret",
                activities=[a for a in activities if a.type in unsupported],
                evidence={"types": dict(sorted(unsupported.items()))},
            )
        )
    foreign = [
        a for a in activities if a.currency and a.currency != config.base_currency
    ]
    if foreign:
        findings.append(
            _finding(
                "non_base_currency",
                "info",
                f"{len(foreign)} activities are not in {config.base_currency}",
                activities=foreign,
                evidence={
                    "currencies": dict(
                        sorted(Counter(a.currency for a in foreign).items())
                    )
                },
            )
        )
    return findings


def audit_portfolio(
    activities: list[Activity],
    accounts: list[AccountInfo],
    snapshots: list[BalanceSnapshot],
    prices: Mapping[str, list[PricePoint]],
    isins: Mapping[str, str | None],
    rows: list[ChartRow],
    config: AuditConfig,
    as_of: date,
) -> dict[str, Any]:
    """Run every check and return the findings, most severe first.

    Args:
        activities: All parsed activities.
        accounts: Accounts with their current cash balance.
        snapshots: Balance history entries of all accounts.
        prices: Stored daily prices per ``DATASOURCE:SYMBOL``.
        isins: ISIN per ``DATASOURCE:SYMBOL`` (None when unknown).
        rows: One daily performance chart response, already cut at ``as_of``.
        config: Calendars, interest rules and thresholds.
        as_of: The last complete day; staleness is measured against it.
    """
    by_id = {account.id: account for account in accounts}
    known = [a for a in activities if a.local_date <= as_of]
    findings = [
        *_check_duplicates(known),
        *_check_far_prices(known, prices, config),
        *_check_trade_dates(known, prices, config),
        *_check_interest(known, by_id, config, as_of),
        *_check_balances(by_id, snapshots, config, as_of),
        *_check_prices(known, prices, config, as_of),
        *_check_chart(rows, known, config),
        *_check_seed(known, config),
        *_check_styles(known, config),
        *_check_misc(known, isins, config),
    ]
    findings.sort(
        key=lambda f: (
            _SEVERITY_RANK[f["severity"]],
            f["check"],
            f["account_id"] or "",
            f["security"] or "",
            f["dates"],
            f["activity_ids"],
        )
    )
    counts = Counter((f["severity"], f["check"]) for f in findings)
    return {
        "method_version": METHOD_VERSION,
        "as_of": as_of.isoformat(),
        "config": {
            "timezone": config.timezone,
            "chart_timezone": config.chart_timezone,
            "base_currency": config.base_currency,
            "interest_rules": {
                account_id: {"attribution": rule.attribution, "expected": rule.expected}
                for account_id, rule in sorted(config.interest_rules.items())
            },
            "no_interest_accounts": sorted(config.no_interest_accounts),
            "thresholds": {
                "trade_date_min_gap": config.trade_date_min_gap,
                "trade_date_exact_gap": config.trade_date_exact_gap,
                "price_far_ratio": config.price_far_ratio,
                "interest_window_days": config.interest_window_days,
                "cash_threshold": config.cash_threshold,
                "stale_balance_days": config.stale_balance_days,
                "stale_price_days": config.stale_price_days,
                "seed_gap_days": config.seed_gap_days,
                "seed_share": config.seed_share,
                "invariant_tolerance": config.invariant_tolerance,
            },
        },
        "summary": {
            "total": len(findings),
            "by_severity": {
                severity: sum(n for (s, _), n in counts.items() if s == severity)
                for severity in ("error", "warn", "info")
            },
            "by_check": {
                check: sum(n for (_, c), n in counts.items() if c == check)
                for check in sorted({c for _, c in counts})
            },
        },
        "findings": findings,
    }


def parse_balance_snapshots(
    account_id: str, raw_balances: list[Mapping[str, Any]]
) -> list[BalanceSnapshot]:
    """Parse ``balances`` of ``GET /api/v1/account/<id>/balances``.

    Balance entries are dates, stored at UTC midnight.
    """
    return sorted(
        (
            BalanceSnapshot(
                account_id=account_id,
                date=datetime.fromisoformat(str(raw["date"])).date(),
                value=float(raw.get("valueInBaseCurrency", raw.get("value")) or 0),
            )
            for raw in raw_balances
        ),
        key=lambda s: (s.account_id, s.date),
    )
