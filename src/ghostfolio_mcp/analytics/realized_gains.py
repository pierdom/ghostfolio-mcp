"""Deterministic FIFO realized gains per sale and tax year.

Pure: no network, no clock, no environment reads. Not tax advice: the module
applies the conventions below, and the caller decides whether they fit.

Conventions (``METHOD_VERSION`` changes whenever one of these does):

- Lots are matched first-in, first-out per asset (``DATASOURCE:SYMBOL``)
  across all accounts, since identical securities are pooled regardless of
  where they are held. Accounts with ``tax.excluded`` (e.g. a pension plan) are
  left out entirely.
- Activities are processed in timestamp order; on a tie a BUY comes first.
- A buy's cost is its value plus its fee; a sale's proceeds are its value
  minus its fee. A partly consumed lot gives up cost in proportion to quantity.
- Dates and tax years are the activity's date in ``timezone``.
- Two-month rule (``wash_sale_months``, 0 to disable): a sale at a loss is
  flagged when the same asset was bought within that many calendar months
  before or after it and those shares were still held after the sale. The
  flag gives the affected quantity and the share of the loss it covers; the
  loss itself is reported unchanged.
- A sale larger than the shares held is reported with the uncovered quantity
  and no gain.
- Only activities in the base currency are supported; others are refused.
"""

import math
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from typing import Any

from ghostfolio_mcp.analytics.common import Activity
from ghostfolio_mcp.analytics.common import money
from ghostfolio_mcp.analytics.common import parse_timestamp
from ghostfolio_mcp.analytics.common import quantity
from ghostfolio_mcp.analytics.common import ratio

METHOD_VERSION = "realized-gains/1"

# A lot below this many shares is treated as used up.
_EPSILON = 1e-9


@dataclass(frozen=True)
class GainsConfig:
    timezone: str = "Europe/Madrid"
    base_currency: str = "EUR"
    excluded_accounts: Collection[str] = field(default_factory=frozenset)
    wash_sale_months: int = 2


@dataclass
class _Lot:
    buy: Activity
    remaining: float
    cost_per_share: float


def add_months(day: date, months: int) -> date:
    """The same day ``months`` calendar months away, clamped to month end."""
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    following = date(year + month // 12, month % 12 + 1, 1)
    last = (following - date.resolution).day
    return date(year, month, min(day.day, last))


def _order(activity: Activity) -> tuple:
    return (
        parse_timestamp(activity.timestamp),
        0 if activity.type == "BUY" else 1,
        activity.id,
    )


def compute_realized_gains(
    activities: list[Activity],
    config: GainsConfig,
    year: int | None = None,
) -> dict[str, Any]:
    """FIFO gains for every sale, or for the sales of one tax year.

    Lots are always built from the whole history; ``year`` only selects which
    sales are reported.

    Raises:
        ValueError: When a BUY or SELL is not in the base currency.
    """
    trades = [
        a
        for a in activities
        if a.type in ("BUY", "SELL") and a.account_id not in config.excluded_accounts
    ]
    foreign = sorted(
        a.id for a in trades if a.currency and a.currency != config.base_currency
    )
    if foreign:
        raise ValueError(
            f"{len(foreign)} trade(s) are not in {config.base_currency} "
            f"(first: {foreign[0]}); currency conversion is not supported"
        )
    trades.sort(key=_order)

    lots: dict[str, list[_Lot]] = defaultdict(list)
    buys_by_security: dict[str, list[Activity]] = defaultdict(list)
    for trade in trades:
        if trade.type == "BUY":
            buys_by_security[trade.security].append(trade)

    sales: list[dict[str, Any]] = []
    for trade in trades:
        if trade.type == "BUY":
            if trade.quantity > 0:
                lots[trade.security].append(
                    _Lot(
                        buy=trade,
                        remaining=trade.quantity,
                        cost_per_share=(trade.amount + trade.fee) / trade.quantity,
                    )
                )
            continue

        to_sell = trade.quantity
        consumed = []
        for lot in lots[trade.security]:
            if to_sell <= _EPSILON:
                break
            if lot.remaining <= _EPSILON:
                continue
            take = min(lot.remaining, to_sell)
            lot.remaining -= take
            to_sell -= take
            consumed.append((lot, take, take * lot.cost_per_share))
        uncovered = to_sell if to_sell > _EPSILON else 0.0
        proceeds = trade.amount - trade.fee
        cost = math.fsum(c for _, _, c in consumed)
        gain = None if uncovered else proceeds - cost

        wash = None
        wash_loss = None
        if gain is not None and gain < 0 and config.wash_sale_months > 0:
            start = add_months(trade.local_date, -config.wash_sale_months)
            end = add_months(trade.local_date, config.wash_sale_months)
            consumed_by_buy = defaultdict(float)
            for lot, take, _ in consumed:
                consumed_by_buy[lot.buy.id] += take
            window = []
            for buy in buys_by_security[trade.security]:
                if buy.id == trade.id or not start <= buy.local_date <= end:
                    continue
                if _order(buy) < _order(trade):
                    # Bought before: only shares this sale did not use up
                    # still count as held after it.
                    lot = next(
                        (lot for lot in lots[trade.security] if lot.buy is buy), None
                    )
                    held = lot.remaining if lot else 0.0
                else:
                    held = buy.quantity
                if held > _EPSILON:
                    window.append((buy, held))
            affected = min(trade.quantity, math.fsum(h for _, h in window))
            if affected > _EPSILON:
                wash_loss = gain * affected / trade.quantity
                wash = {
                    "window": [start.isoformat(), end.isoformat()],
                    "affected_quantity": quantity(affected),
                    "affected_share": ratio(affected / trade.quantity),
                    "affected_loss": money(wash_loss),
                    "buys": [
                        {
                            "activity_id": buy.id,
                            "date": buy.local_date.isoformat(),
                            "account_id": buy.account_id,
                            "quantity_held": quantity(held),
                        }
                        for buy, held in window
                    ],
                }

        sales.append(
            {
                "activity_id": trade.id,
                "date": trade.local_date.isoformat(),
                "tax_year": trade.local_date.year,
                "account_id": trade.account_id,
                "account_name": trade.account_name,
                "security": trade.security,
                "quantity": quantity(trade.quantity),
                "proceeds": money(proceeds),
                "fee": money(trade.fee),
                "cost": money(cost),
                "gain": None if gain is None else money(gain),
                "lots": [
                    {
                        "buy_activity_id": lot.buy.id,
                        "buy_date": lot.buy.local_date.isoformat(),
                        "account_id": lot.buy.account_id,
                        "quantity": quantity(take),
                        "cost": money(lot_cost),
                        "holding_days": (trade.local_date - lot.buy.local_date).days,
                    }
                    for lot, take, lot_cost in consumed
                ],
                "flags": {
                    "uncovered_quantity": quantity(uncovered),
                    "wash_sale": wash,
                },
                "_gain": gain,
                "_wash_loss": wash_loss,
            }
        )

    reported = [s for s in sales if year is None or s["tax_year"] == year]
    years: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for sale in reported:
        years[sale["tax_year"]].append(sale)

    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        gains = [s["_gain"] for s in items if s["_gain"] is not None and s["_gain"] > 0]
        losses = [
            s["_gain"] for s in items if s["_gain"] is not None and s["_gain"] < 0
        ]
        flagged = [s["_wash_loss"] for s in items if s["_wash_loss"] is not None]
        return {
            "sales": len(items),
            "gains": money(math.fsum(gains)),
            "losses": money(math.fsum(losses)),
            "net": money(math.fsum(gains + losses)),
            "proceeds": money(math.fsum(s["proceeds"] for s in items)),
            "losses_flagged_two_month_rule": money(math.fsum(flagged)),
            "sales_with_uncovered_quantity": sum(
                1 for s in items if s["flags"]["uncovered_quantity"]
            ),
        }

    open_lots = []
    for security in sorted(lots):
        remaining = [lot for lot in lots[security] if lot.remaining > _EPSILON]
        if not remaining:
            continue
        open_lots.append(
            {
                "security": security,
                "quantity": quantity(math.fsum(lot.remaining for lot in remaining)),
                "cost": money(
                    math.fsum(lot.remaining * lot.cost_per_share for lot in remaining)
                ),
                "lots": len(remaining),
            }
        )

    return {
        "method_version": METHOD_VERSION,
        "config": {
            "timezone": config.timezone,
            "base_currency": config.base_currency,
            "excluded_accounts": sorted(config.excluded_accounts),
            "wash_sale_months": config.wash_sale_months,
            "lot_matching": "fifo_per_asset_across_accounts",
        },
        "year": year,
        "sales": [
            {k: v for k, v in sale.items() if not k.startswith("_")}
            for sale in reported
        ],
        "by_year": {str(y): summarize(items) for y, items in sorted(years.items())},
        "open_lots": open_lots,
    }
