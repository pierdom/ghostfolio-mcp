"""Tests for Modelo 720 inputs. All data is synthetic; thresholds are scaled
down so amounts stay tiny."""

import json
from datetime import date

import pytest

from ghostfolio_mcp.analytics.audit import AccountInfo
from ghostfolio_mcp.analytics.audit import BalanceSnapshot
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import parse_activities
from ghostfolio_mcp.analytics.modelo720 import METHOD_VERSION
from ghostfolio_mcp.analytics.modelo720 import Modelo720Config
from ghostfolio_mcp.analytics.modelo720 import compute_modelo720_inputs

TZ = "Europe/Madrid"
FOREIGN = AccountInfo("acc-f", "Foreign", 0.0)
FOREIGN_EXCLUDED = AccountInfo(
    "acc-x", "Foreign bank", 0.0, excluded_from_analysis=True
)
DOMESTIC = AccountInfo("acc-d", "Domestic", 0.0)


def trade(id_, type_, timestamp, qty, account="acc-f", symbol="ETF", currency="EUR"):
    return {
        "id": id_,
        "type": type_,
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": account},
        "SymbolProfile": {"symbol": symbol, "dataSource": "YAHOO"},
        "quantity": qty,
        "unitPrice": 1.0,
        "feeInBaseCurrency": 0,
        "valueInBaseCurrency": qty,
        "currency": currency,
    }


def snap(account, day, value):
    return BalanceSnapshot(account, date.fromisoformat(day), value)


def prices(*points):
    return [PricePoint(date.fromisoformat(d), p) for d, p in points]


def run(
    *,
    accounts=(FOREIGN, FOREIGN_EXCLUDED, DOMESTIC),
    snapshots=(),
    activities=(),
    price_map=None,
    year=2025,
    as_of="2026-02-01",
    previous=None,
    **config,
):
    config.setdefault("timezone", TZ)
    config.setdefault("foreign_accounts", {"acc-f", "acc-x"})
    config.setdefault("threshold", 5.0)
    config.setdefault("increase_threshold", 2.0)
    return compute_modelo720_inputs(
        list(accounts),
        list(snapshots),
        parse_activities(activities, TZ, "UTC"),
        price_map or {},
        Modelo720Config(**config),
        year,
        date.fromisoformat(as_of),
        previous,
    )


def account_row(result, account_id):
    return next(a for a in result["accounts"] if a["account_id"] == account_id)


def test_year_end_cash_and_q4_average_from_snapshots():
    result = run(
        snapshots=[
            snap("acc-f", "2025-09-15", 1.0),
            snap("acc-f", "2025-11-16", 3.0),  # Oct 1..Nov 15: 46 days at 1.00
            snap("acc-f", "2026-01-10", 9.0),  # after year end: ignored
        ]
    )
    cash = account_row(result, "acc-f")["cash"]

    assert result["valuation_date"] == "2025-12-31"
    assert result["year_complete"] is True
    assert cash["balance"] == 3.0
    assert cash["balance_snapshot_date"] == "2025-11-16"
    assert cash["q4_days"] == 92
    assert cash["q4_covered_days"] == 92
    assert cash["q4_average"] == pytest.approx((46 * 1.0 + 46 * 3.0) / 92, abs=0.005)


def test_only_foreign_accounts_are_included_even_if_excluded_from_analysis():
    result = run(
        snapshots=[snap("acc-x", "2025-01-01", 1.0), snap("acc-d", "2025-01-01", 9.0)]
    )

    assert [a["account_id"] for a in result["accounts"]] == ["acc-f", "acc-x"]
    assert account_row(result, "acc-x")["excluded_from_analysis"] is True


def test_securities_valued_at_the_last_close_on_or_before_year_end():
    result = run(
        activities=[
            trade("b1", "BUY", "2025-03-01T10:00:00Z", 2),
            trade("s1", "SELL", "2025-06-01T10:00:00Z", 0.5),
            trade("b2", "BUY", "2025-12-31T23:30:00Z", 5),  # Jan 1 local: next year
            trade("b3", "BUY", "2025-03-01T10:00:00Z", 1, symbol="GONE"),
            trade("s3", "SELL", "2025-04-01T10:00:00Z", 1, symbol="GONE"),
            trade("b4", "BUY", "2025-03-01T10:00:00Z", 1, account="acc-d"),
        ],
        price_map={"YAHOO:ETF": prices(("2025-12-30", 2.0), ("2026-01-02", 9.0))},
    )
    [position] = account_row(result, "acc-f")["securities"]["positions"]

    assert position == {
        "security": "YAHOO:ETF",
        "quantity": 1.5,
        "price": 2.0,
        "price_date": "2025-12-30",
        "value": 3.0,
        "flags": {"no_price": False, "stale_price": False},
    }
    assert result["categories"]["securities"]["year_end_value"] == 3.0


def test_missing_and_stale_prices_are_flagged():
    result = run(
        activities=[
            trade("b1", "BUY", "2025-03-01T10:00:00Z", 1),
            trade("b2", "BUY", "2025-03-01T10:00:00Z", 1, symbol="NOPRICE"),
        ],
        price_map={"YAHOO:ETF": prices(("2025-12-01", 1.0))},
    )
    row = account_row(result, "acc-f")
    flags = {p["security"]: p["flags"] for p in row["securities"]["positions"]}

    assert flags == {
        "YAHOO:ETF": {"no_price": False, "stale_price": True},
        "YAHOO:NOPRICE": {"no_price": True, "stale_price": False},
    }
    assert row["flags"]["positions_without_price"] == 1


def test_running_year_is_a_preview_as_of_yesterday():
    result = run(
        snapshots=[snap("acc-f", "2026-01-01", 1.0)],
        year=2026,
        as_of="2026-10-09",
    )
    cash = account_row(result, "acc-f")["cash"]

    assert result["valuation_date"] == "2026-10-09"
    assert result["year_complete"] is False
    assert cash["q4_days"] == 9
    assert cash["q4_average"] == 1.0


def test_before_q4_there_is_no_q4_average():
    result = run(
        snapshots=[snap("acc-f", "2026-01-01", 1.0)], year=2026, as_of="2026-06-30"
    )
    cash = account_row(result, "acc-f")["cash"]

    assert cash["q4_average"] is None
    assert cash["q4_days"] == 0


def test_partial_coverage_and_missing_history_are_flagged():
    result = run(snapshots=[snap("acc-f", "2025-11-01", 1.0)])

    assert account_row(result, "acc-f")["flags"]["q4_partially_covered"] is True
    assert account_row(result, "acc-f")["cash"]["q4_covered_days"] == 61
    assert account_row(result, "acc-x")["flags"]["no_balance_history"] is True


def test_thresholds_use_the_larger_cash_figure_and_previous_declaration():
    result = run(
        snapshots=[snap("acc-f", "2025-01-01", 8.0), snap("acc-f", "2025-12-20", 1.0)],
        activities=[trade("b1", "BUY", "2025-03-01T10:00:00Z", 2)],
        price_map={"YAHOO:ETF": prices(("2025-12-31", 3.0))},
        previous={"accounts": 4.0, "securities": 5.0},
    )
    accounts = result["categories"]["accounts"]
    securities = result["categories"]["securities"]

    # Year end is 1.00 but the Q4 average is higher, and that one counts.
    assert accounts["year_end_balance"] == 1.0
    assert accounts["measured_value"] == accounts["q4_average_balance"]
    assert accounts["exceeds_threshold"] is True
    assert accounts["increase_over_previous"] == pytest.approx(
        accounts["measured_value"] - 4.0, abs=0.01
    )
    assert accounts["increase_exceeds_threshold"] is True
    assert securities["measured_value"] == 6.0
    assert securities["exceeds_threshold"] is True
    assert securities["increase_over_previous"] == 1.0
    assert securities["increase_exceeds_threshold"] is False


def test_future_year_and_foreign_currency_are_refused():
    with pytest.raises(ValueError, match="has not started"):
        run(year=2027, as_of="2026-10-09")
    with pytest.raises(ValueError, match="not in EUR"):
        run(activities=[trade("b1", "BUY", "2025-03-01T10:00:00Z", 1, currency="USD")])


def test_output_is_byte_identical_and_order_independent():
    kwargs = {
        "price_map": {"YAHOO:ETF": prices(("2025-12-31", 3.0))},
        "previous": {"accounts": 1.0},
    }
    snapshots = [snap("acc-f", "2025-01-01", 1.0), snap("acc-x", "2025-02-01", 2.0)]
    activities = [
        trade("b1", "BUY", "2025-03-01T10:00:00Z", 2),
        trade("b2", "BUY", "2025-04-01T10:00:00Z", 1, account="acc-x"),
    ]
    first = run(snapshots=snapshots, activities=activities, **kwargs)
    second = run(
        accounts=(DOMESTIC, FOREIGN_EXCLUDED, FOREIGN),
        snapshots=snapshots[::-1],
        activities=activities[::-1],
        **kwargs,
    )

    assert first["method_version"] == METHOD_VERSION
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
