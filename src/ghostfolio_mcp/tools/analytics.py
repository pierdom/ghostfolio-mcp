"""
Analytics tools computed server-side from Ghostfolio data
"""

import logging
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import Annotated
from typing import Any
from typing import Literal
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from ghostfolio_mcp.analytics.audit import AccountInfo
from ghostfolio_mcp.analytics.audit import AuditConfig
from ghostfolio_mcp.analytics.audit import BalanceSnapshot
from ghostfolio_mcp.analytics.audit import audit_portfolio
from ghostfolio_mcp.analytics.audit import parse_balance_snapshots
from ghostfolio_mcp.analytics.benchmark import BenchmarkConfig
from ghostfolio_mcp.analytics.benchmark import compare_with_benchmark
from ghostfolio_mcp.analytics.cash_yield import CashYieldConfig
from ghostfolio_mcp.analytics.cash_yield import compute_cash_yield
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import parse_market_data
from ghostfolio_mcp.analytics.import_preview import PreviewConfig
from ghostfolio_mcp.analytics.import_preview import preview_import as run_import_preview
from ghostfolio_mcp.analytics.modelo720 import Modelo720Config
from ghostfolio_mcp.analytics.modelo720 import compute_modelo720_inputs
from ghostfolio_mcp.analytics.monthly_returns import InterestRule
from ghostfolio_mcp.analytics.monthly_returns import ReturnsConfig
from ghostfolio_mcp.analytics.monthly_returns import compute_monthly_returns
from ghostfolio_mcp.analytics.monthly_returns import month_start
from ghostfolio_mcp.analytics.monthly_returns import parse_activities
from ghostfolio_mcp.analytics.monthly_returns import parse_chart
from ghostfolio_mcp.analytics.realized_gains import GainsConfig
from ghostfolio_mcp.analytics.realized_gains import compute_realized_gains
from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.utils import quote_path_segment

logger = logging.getLogger(__name__)

# The Ghostfolio server's own calendar, which it buckets chart days on.
CHART_TIMEZONE = "UTC"

# Only these ranges come back at daily resolution; longer ones (max, 5y, whole
# years) skip days, which would silently merge several days into one delta.
# Ordered shortest first so the smallest sufficient window is used.
DAILY_CHART_RANGES = ("mtd", "ytd", "1y")


def _today(timezone: str) -> date:
    """Today's date in a timezone. Separate so tests can pin the clock."""
    return datetime.now(ZoneInfo(timezone)).date()


def _check_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolError(f"Unknown timezone {timezone!r}") from exc


def _interest_rules(config: GhostfolioConfig) -> dict[str, InterestRule]:
    """Interest rules of the accounts whose profile has an interest section.

    An account without one gets the defaults wherever a rule is looked up;
    leaving it out here keeps the echoed config to what was configured, so
    an account is not shown as paying no interest just because it isn't set.
    """
    return {
        account_id: InterestRule(
            attribution=profile.interest.attribution,
            expected=profile.interest.expected,
        )
        for account_id, profile in config.account_profiles.items()
        if "interest" in profile.model_fields_set
    }


def _no_interest_accounts(config: GhostfolioConfig) -> frozenset[str]:
    """Accounts whose profile explicitly sets interest.expected to "none".

    Left unset, "none" is only the default and says nothing about the
    account; set explicitly, it states the account pays no interest.
    """
    return frozenset(
        account_id
        for account_id, profile in config.account_profiles.items()
        if "interest" in profile.model_fields_set
        and "expected" in profile.interest.model_fields_set
        and profile.interest.expected == "none"
    )


def _security_key(data_source: str, symbol: str) -> str:
    return f"{data_source}:{symbol}"


async def _fetch_daily_chart(client: Any, start_month: str) -> tuple[list, str]:
    """The shortest daily chart with a row before ``start_month``, and its range."""
    window_start = month_start(start_month)
    for candidate in DAILY_CHART_RANGES:
        performance = await client.get(
            "portfolio/performance", params={"range": candidate}, api_version="v2"
        )
        rows = parse_chart(performance.get("chart") or [])
        if rows and rows[0].date < window_start:
            return rows, candidate
    raise ToolError(
        f"No daily chart reaches back before {start_month}; the daily ranges "
        f"({', '.join(DAILY_CHART_RANGES)}) cover about the last year only"
    )


async def _resolve_benchmark(
    client: Any, config: GhostfolioConfig, requested: str | None
) -> tuple[str, str]:
    """(dataSource, symbol): the argument, DEFAULT_BENCHMARK, or the user's."""
    choice = (requested or "").strip() or config.default_benchmark
    if not choice:
        user = await client.get("user")
        benchmark_id = (user.get("settings") or {}).get("benchmark")
        info = await client.get("info")
        match = next(
            (b for b in info.get("benchmarks") or [] if b.get("id") == benchmark_id),
            None,
        )
        if match is None:
            raise ToolError(
                "No benchmark given, DEFAULT_BENCHMARK is unset and the Ghostfolio "
                "user settings select none"
            )
        return match["dataSource"], match["symbol"]
    data_source, _, symbol = choice.partition(":")
    if not data_source or not symbol:
        raise ToolError(f"Benchmark must be DATASOURCE:SYMBOL, got {choice!r}")
    return data_source, symbol


async def _fetch_prices(
    client: Any, securities: list[tuple[str, str]]
) -> tuple[dict[str, list[PricePoint]], dict[str, str | None]]:
    """Stored daily prices and ISIN per DATASOURCE:SYMBOL, read-only."""
    prices: dict[str, list[PricePoint]] = {}
    isins: dict[str, str | None] = {}
    for data_source, symbol in sorted(set(securities)):
        asset = await client.get(
            f"asset/{quote_path_segment(data_source)}/{quote_path_segment(symbol)}"
        )
        key = _security_key(data_source, symbol)
        prices[key] = parse_market_data(asset.get("marketData") or [])
        isins[key] = (asset.get("assetProfile") or {}).get("isin") or None
    return prices, isins


async def _fetch_accounts(
    client: Any,
) -> tuple[list[AccountInfo], list[BalanceSnapshot]]:
    """Accounts with their cash balance, plus every balance-history entry."""
    raw_accounts = (await client.get("account")).get("accounts") or []
    accounts = []
    snapshots: list[BalanceSnapshot] = []
    for raw in sorted(raw_accounts, key=lambda a: a["id"]):
        tags = {str(tag.get("name")) for tag in raw.get("tags") or []}
        accounts.append(
            AccountInfo(
                id=raw["id"],
                name=raw.get("name") or "",
                balance=float(
                    raw.get("balanceInBaseCurrency", raw.get("balance")) or 0
                ),
                excluded_from_analysis="EXCLUDE_FROM_ANALYSIS" in tags,
            )
        )
        history = await client.get(f"account/{quote_path_segment(raw['id'])}/balances")
        snapshots.extend(
            parse_balance_snapshots(raw["id"], history.get("balances") or [])
        )
    return accounts, snapshots


def register_analytics_tools(mcp: FastMCP, config: GhostfolioConfig) -> None:
    """Register analytics Ghostfolio tools with the FastMCP server."""

    @mcp.tool(
        tags={"portfolio", "performance", "analytics", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_monthly_returns(
        start_month: Annotated[
            str, Field(description="First month to report, YYYY-MM")
        ],
        end_month: Annotated[str, Field(description="Last month to report, YYYY-MM")],
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone activity dates are bucketed in",
            ),
        ] = "Europe/Madrid",
        interest_attribution: Annotated[
            Literal["payment_date", "accrual"],
            Field(
                default="payment_date",
                description=(
                    "'payment_date' books interest in the month it was paid; "
                    "'accrual' applies the per-account interest rules from ACCOUNT_PROFILES"
                ),
            ),
        ] = "payment_date",
    ) -> dict[str, Any]:
        """
        Get deterministic monthly returns of the securities portfolio.

        Computed server-side from the daily performance chart and the activity
        list, so the same Ghostfolio data always gives the same figures. Per
        month: net and gross market P&L, fees, interest (per account), net
        flows at cost, average invested capital, time-weighted return and
        Modified Dietz return, plus data-quality flags. A period summary chains
        the same measures over the whole window. Returns cover the securities
        sleeve only (cash excluded) and are net of fees; interest is reported
        next to them, not inside them. Today's chart row is left out because
        its prices are still moving.

        Args:
            start_month: First month to report, YYYY-MM
            end_month: Last month to report, YYYY-MM
            timezone: IANA timezone activity dates are bucketed in
            interest_attribution: 'payment_date' or 'accrual'

        Returns:
            Dictionary with method_version, config, window, months and summary
        """
        _check_timezone(timezone)

        returns_config = ReturnsConfig(
            timezone=timezone,
            chart_timezone=CHART_TIMEZONE,
            interest_attribution=interest_attribution,
            interest_rules=_interest_rules(config),
        )
        try:
            month_start(start_month)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        async with get_ghostfolio_client(config) as client:
            rows, chart_range = await _fetch_daily_chart(client, start_month)
            raw_activities = (await client.get("activities")).get("activities") or []

        activities = parse_activities(raw_activities, timezone, CHART_TIMEZONE)
        yesterday = _today(CHART_TIMEZONE) - timedelta(days=1)
        try:
            result = compute_monthly_returns(
                rows,
                activities,
                returns_config,
                start_month,
                end_month,
                last_complete_date=yesterday,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        result["window"]["chart_range"] = chart_range
        return result

    @mcp.tool(
        tags={"portfolio", "analytics", "audit", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def audit_portfolio_data(
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone activity dates are read in",
            ),
        ] = "Europe/Madrid",
    ) -> dict[str, Any]:
        """
        Audit the portfolio data for problems that skew analytics.

        Read-only and deterministic. Checks activities, accounts, balance
        history, stored prices and the daily performance chart, and returns
        findings with a severity (error, warn, info), the activities and dates
        involved and the evidence: trades booked a day off because of their
        timestamp, prices far from the close, duplicates, misdated or missing
        interest, cash without interest, stale balances and prices, chart gaps
        and invariant breaks, a legacy seed position, mixed timestamp styles,
        and assets without ISIN. Nothing is corrected.

        Args:
            timezone: IANA timezone activity dates are read in

        Returns:
            Dictionary with method_version, as_of, config, summary and findings
        """
        _check_timezone(timezone)
        as_of = _today(timezone) - timedelta(days=1)
        async with get_ghostfolio_client(config) as client:
            raw_activities = (await client.get("activities")).get("activities") or []
            activities = parse_activities(raw_activities, timezone, CHART_TIMEZONE)
            accounts, snapshots = await _fetch_accounts(client)
            performance = await client.get(
                "portfolio/performance", params={"range": "1y"}, api_version="v2"
            )
            prices, isins = await _fetch_prices(
                client,
                [
                    (a.data_source, a.symbol)
                    for a in activities
                    if a.type in ("BUY", "SELL")
                ],
            )
            user = await client.get("user")
        rows = [
            r for r in parse_chart(performance.get("chart") or []) if r.date <= as_of
        ]
        base_currency = (user.get("settings") or {}).get("baseCurrency") or "EUR"
        return audit_portfolio(
            activities,
            accounts,
            snapshots,
            prices,
            isins,
            rows,
            AuditConfig(
                timezone=timezone,
                chart_timezone=CHART_TIMEZONE,
                base_currency=base_currency,
                interest_rules=_interest_rules(config),
                no_interest_accounts=_no_interest_accounts(config),
            ),
            as_of,
        )

    @mcp.tool(
        tags={"portfolio", "analytics", "cash", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_cash_yield(
        start_month: Annotated[
            str, Field(description="First month to report, YYYY-MM")
        ],
        end_month: Annotated[str, Field(description="Last month to report, YYYY-MM")],
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone interest dates are bucketed in",
            ),
        ] = "Europe/Madrid",
        interest_attribution: Annotated[
            Literal["payment_date", "accrual"],
            Field(
                default="payment_date",
                description=(
                    "'payment_date' books interest in the month it was paid; "
                    "'accrual' applies the per-account interest rules from "
                    "ACCOUNT_PROFILES"
                ),
            ),
        ] = "payment_date",
    ) -> dict[str, Any]:
        """
        Get the effective interest yield on cash, per account and month.

        Read-only and deterministic. Average balance comes from each
        account's balance history (a balance holds until the next snapshot);
        yield is interest over average balance, annualized on ACT/365. A
        period not fully covered by snapshots gets no yield rather than a
        misleading one, and the portfolio total lists the accounts it leaves
        out. Measured up to yesterday.

        Args:
            start_month: First month to report, YYYY-MM
            end_month: Last month to report, YYYY-MM
            timezone: IANA timezone interest dates are bucketed in
            interest_attribution: 'payment_date' or 'accrual'

        Returns:
            Dictionary with method_version, config, window, months and summary
        """
        _check_timezone(timezone)
        try:
            month_start(start_month)
            month_start(end_month)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        as_of = _today(timezone) - timedelta(days=1)
        async with get_ghostfolio_client(config) as client:
            raw_activities = (await client.get("activities")).get("activities") or []
            accounts, snapshots = await _fetch_accounts(client)
        activities = parse_activities(raw_activities, timezone, CHART_TIMEZONE)
        try:
            return compute_cash_yield(
                accounts,
                snapshots,
                activities,
                CashYieldConfig(
                    timezone=timezone,
                    interest_attribution=interest_attribution,
                    interest_rules=_interest_rules(config),
                ),
                start_month,
                end_month,
                as_of,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        tags={"import", "analytics", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def preview_import(
        data: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "The payload you would pass to import_transactions: an "
                    "'activities' list of objects with currency, dataSource, "
                    "date, quantity, symbol, type, unitPrice, and usually fee "
                    "and accountId"
                )
            ),
        ],
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone activities are compared by date in",
            ),
        ] = "Europe/Madrid",
    ) -> dict[str, Any]:
        """
        Dry-run an activity import: find duplicates before anything is written.

        Read-only and deterministic; nothing is imported. Each proposed
        activity is classified against the existing activities and the
        earlier items of the same batch: 'duplicate' (same account, type,
        asset, local date, quantity, price and fee), 'conflict' (same account,
        type, asset and date but different values, or a second payment on one
        day from a daily interest payer), 'invalid' (missing or malformed
        fields) or 'new'. Warnings flag local-midnight timestamps (booked on
        the previous UTC day), date-only values, future dates and unknown
        accounts. Run it before import_transactions or create_activity.

        Args:
            data: The import_transactions payload
            timezone: IANA timezone activities are compared by date in

        Returns:
            Dictionary with method_version, config, today, summary and items
        """
        _check_timezone(timezone)
        proposed = data.get("activities")
        if not isinstance(proposed, list) or not all(
            isinstance(item, dict) for item in proposed
        ):
            raise ToolError("data must contain an 'activities' list of objects")
        async with get_ghostfolio_client(config) as client:
            raw_activities = (await client.get("activities")).get("activities") or []
            raw_accounts = (await client.get("account")).get("accounts") or []
        return run_import_preview(
            proposed,
            parse_activities(raw_activities, timezone, CHART_TIMEZONE),
            {a["id"]: a.get("name") or "" for a in raw_accounts},
            PreviewConfig(timezone=timezone, interest_rules=_interest_rules(config)),
            _today(timezone),
        )

    @mcp.tool(
        tags={"portfolio", "analytics", "tax", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_realized_gains(
        year: Annotated[
            int | None,
            Field(
                default=None,
                description="Tax year to report sales for; omit for every sale",
            ),
        ] = None,
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone sale dates and tax years are read in",
            ),
        ] = "Europe/Madrid",
    ) -> dict[str, Any]:
        """
        Get realized gains per sale with FIFO lot matching, and per tax year.

        Read-only and deterministic; not tax advice. Lots are matched
        first-in, first-out per asset across all accounts; accounts with
        tax.excluded in ACCOUNT_PROFILES (e.g. a pension plan) are left out.
        Buy fees add to cost, sell fees reduce proceeds. A loss with the same
        asset bought within two months before or after and still held is
        flagged (two-month rule) with the affected quantity and loss; the
        loss is not changed. Each sale lists the lots it consumed; open lots
        are returned for reconciliation. Only base-currency trades are
        supported.

        Args:
            year: Tax year to report sales for; omit for every sale
            timezone: IANA timezone sale dates and tax years are read in

        Returns:
            Dictionary with method_version, config, year, sales, by_year and open_lots
        """
        _check_timezone(timezone)
        async with get_ghostfolio_client(config) as client:
            raw_activities = (await client.get("activities")).get("activities") or []
            user = await client.get("user")
        try:
            return compute_realized_gains(
                parse_activities(raw_activities, timezone, CHART_TIMEZONE),
                GainsConfig(
                    timezone=timezone,
                    base_currency=(user.get("settings") or {}).get("baseCurrency")
                    or "EUR",
                    excluded_accounts=frozenset(
                        account_id
                        for account_id, profile in config.account_profiles.items()
                        if profile.tax.excluded
                    ),
                ),
                year,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        tags={"portfolio", "analytics", "tax", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_modelo720_inputs(
        year: Annotated[int, Field(description="Year to report, e.g. 2026")],
        previous_declared_accounts: Annotated[
            float | None,
            Field(
                default=None,
                description="Accounts total in the last Modelo 720 filed, if any",
            ),
        ] = None,
        previous_declared_securities: Annotated[
            float | None,
            Field(
                default=None,
                description="Securities total in the last Modelo 720 filed, if any",
            ),
        ] = None,
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone trade dates are read in",
            ),
        ] = "Europe/Madrid",
    ) -> dict[str, Any]:
        """
        Get the figures Modelo 720 asks about for accounts held abroad.

        Read-only and deterministic; not tax advice. Covers the accounts with
        modelo720.foreign in ACCOUNT_PROFILES. Per account: cash balance on
        Dec 31 and its Q4 average (from balance history), and securities held
        on Dec 31 valued at the last stored close. Per category (accounts,
        securities): the total, whether it exceeds 50,000 (for cash, the larger
        of year-end and Q4 average), and the increase over the previously
        declared value against the 20,000 rule. A year still running is
        valued as of yesterday and marked year_complete=false.

        Args:
            year: Year to report, e.g. 2026
            previous_declared_accounts: Accounts total last declared, if any
            previous_declared_securities: Securities total last declared, if any
            timezone: IANA timezone trade dates are read in

        Returns:
            Dictionary with method_version, config, valuation_date, accounts and categories
        """
        _check_timezone(timezone)
        foreign = frozenset(
            account_id
            for account_id, profile in config.account_profiles.items()
            if profile.modelo720.foreign
        )
        if not foreign:
            raise ToolError(
                "No account is marked modelo720.foreign in ACCOUNT_PROFILES"
            )
        as_of = _today(timezone) - timedelta(days=1)
        async with get_ghostfolio_client(config) as client:
            raw_activities = (await client.get("activities")).get("activities") or []
            activities = parse_activities(raw_activities, timezone, CHART_TIMEZONE)
            accounts, snapshots = await _fetch_accounts(client)
            prices, _isins = await _fetch_prices(
                client,
                [
                    (a.data_source, a.symbol)
                    for a in activities
                    if a.type in ("BUY", "SELL") and a.account_id in foreign
                ],
            )
            user = await client.get("user")
        previous = {
            name: value
            for name, value in (
                ("accounts", previous_declared_accounts),
                ("securities", previous_declared_securities),
            )
            if value is not None
        }
        try:
            return compute_modelo720_inputs(
                accounts,
                snapshots,
                activities,
                prices,
                Modelo720Config(
                    timezone=timezone,
                    base_currency=(user.get("settings") or {}).get("baseCurrency")
                    or "EUR",
                    foreign_accounts=foreign,
                ),
                year,
                as_of,
                previous,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(
        tags={"portfolio", "performance", "analytics", "benchmark", "read-only"},
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        },
    )
    async def get_benchmark_comparison(
        start_month: Annotated[
            str, Field(description="First month to report, YYYY-MM")
        ],
        end_month: Annotated[str, Field(description="Last month to report, YYYY-MM")],
        benchmark: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Benchmark as DATASOURCE:SYMBOL, e.g. YAHOO:VWCE.DE; defaults "
                    "to DEFAULT_BENCHMARK, then the Ghostfolio user's benchmark"
                ),
            ),
        ] = None,
        timezone: Annotated[
            str,
            Field(
                default="Europe/Madrid",
                description="IANA timezone interest dates are bucketed in",
            ),
        ] = "Europe/Madrid",
        interest_attribution: Annotated[
            Literal["payment_date", "accrual"],
            Field(
                default="payment_date",
                description=(
                    "'payment_date' or 'accrual' (per-account interest rules from "
                    "ACCOUNT_PROFILES), for the interest set against cash drag"
                ),
            ),
        ] = "payment_date",
    ) -> dict[str, Any]:
        """
        Compare the portfolio with a benchmark per month, and measure cash drag.

        Read-only and deterministic, on the same daily chart and flows as
        get_monthly_returns. Per month and over the period: the portfolio's
        TWR next to the benchmark's return and the excess; the portfolio's
        net market P&L next to what a benchmark holding with the same opening
        value and the same flows would have made; and cash drag - what the
        cash held would have earned in the benchmark, minus the interest it
        actually earned (positive means holding cash cost money). The
        benchmark is any asset with stored prices; an accumulating fund's
        price return is its total return. Today's chart row is left out.

        Args:
            start_month: First month to report, YYYY-MM
            end_month: Last month to report, YYYY-MM
            benchmark: DATASOURCE:SYMBOL; defaults as described
            timezone: IANA timezone interest dates are bucketed in
            interest_attribution: 'payment_date' or 'accrual'

        Returns:
            Dictionary with method_version, config, window, months and summary
        """
        _check_timezone(timezone)
        try:
            month_start(start_month)
            month_start(end_month)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        async with get_ghostfolio_client(config) as client:
            data_source, symbol = await _resolve_benchmark(client, config, benchmark)
            rows, chart_range = await _fetch_daily_chart(client, start_month)
            raw_activities = (await client.get("activities")).get("activities") or []
            accounts, snapshots = await _fetch_accounts(client)
            prices, _isins = await _fetch_prices(client, [(data_source, symbol)])
        excluded = frozenset(a.id for a in accounts if a.excluded_from_analysis)
        cash_known_from = min(
            (s.date for s in snapshots if s.account_id not in excluded), default=None
        )
        key = _security_key(data_source, symbol)
        try:
            result = compare_with_benchmark(
                rows,
                prices[key],
                parse_activities(raw_activities, timezone, CHART_TIMEZONE),
                BenchmarkConfig(
                    benchmark=key,
                    timezone=timezone,
                    chart_timezone=CHART_TIMEZONE,
                    interest_attribution=interest_attribution,
                    interest_rules=_interest_rules(config),
                    excluded_accounts=excluded,
                ),
                start_month,
                end_month,
                last_complete_date=_today(CHART_TIMEZONE) - timedelta(days=1),
                cash_known_from=cash_known_from,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        result["window"]["chart_range"] = chart_range
        return result
