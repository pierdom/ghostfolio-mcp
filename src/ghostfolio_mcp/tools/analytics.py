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

from ghostfolio_mcp.analytics.monthly_returns import InterestRule
from ghostfolio_mcp.analytics.monthly_returns import ReturnsConfig
from ghostfolio_mcp.analytics.monthly_returns import compute_monthly_returns
from ghostfolio_mcp.analytics.monthly_returns import month_start
from ghostfolio_mcp.analytics.monthly_returns import parse_activities
from ghostfolio_mcp.analytics.monthly_returns import parse_chart
from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_client
from ghostfolio_mcp.models import GhostfolioConfig

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
                    "'accrual' applies the per-account rules from INTEREST_RULES"
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
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ToolError(f"Unknown timezone {timezone!r}") from exc

        returns_config = ReturnsConfig(
            timezone=timezone,
            chart_timezone=CHART_TIMEZONE,
            interest_attribution=interest_attribution,
            interest_rules={
                account_id: InterestRule(
                    attribution=rule.attribution, expected=rule.expected
                )
                for account_id, rule in config.interest_rules.items()
            },
        )
        try:
            window_start = month_start(start_month)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        async with get_ghostfolio_client(config) as client:
            rows = None
            chart_range = None
            for candidate in DAILY_CHART_RANGES:
                performance = await client.get(
                    "portfolio/performance",
                    params={"range": candidate},
                    api_version="v2",
                )
                parsed = parse_chart(performance.get("chart") or [])
                if parsed and parsed[0].date < window_start:
                    rows, chart_range = parsed, candidate
                    break
            if rows is None:
                raise ToolError(
                    f"No daily chart reaches back before {start_month}; the "
                    f"daily ranges ({', '.join(DAILY_CHART_RANGES)}) cover about "
                    "the last year only"
                )
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
