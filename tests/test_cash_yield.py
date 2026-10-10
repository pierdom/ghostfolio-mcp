"""Tests for the cash yield module. All data is synthetic and tiny."""

import json
from datetime import date

import pytest

from ghostfolio_mcp.analytics.audit import AccountInfo
from ghostfolio_mcp.analytics.audit import BalanceSnapshot
from ghostfolio_mcp.analytics.cash_yield import METHOD_VERSION
from ghostfolio_mcp.analytics.cash_yield import CashYieldConfig
from ghostfolio_mcp.analytics.cash_yield import compute_cash_yield
from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import parse_activities

TZ = "Europe/Madrid"
A = AccountInfo("acc-a", "A", 2.0)
B = AccountInfo("acc-b", "B", 1.0)


def snap(account, day, value):
    return BalanceSnapshot(account, date.fromisoformat(day), value)


def interest(id_, timestamp, amount, account="acc-a"):
    return {
        "id": id_,
        "type": "INTEREST",
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": account.upper()},
        "SymbolProfile": {"symbol": "GF_INTEREST-CASH", "dataSource": "MANUAL"},
        "quantity": 1,
        "unitPrice": amount,
        "valueInBaseCurrency": amount,
        "feeInBaseCurrency": 0,
    }


def run(
    snapshots,
    activities=(),
    accounts=(A,),
    start="2026-04",
    end="2026-04",
    as_of="2026-05-31",
    **config,
):
    config.setdefault("timezone", TZ)
    return compute_cash_yield(
        list(accounts),
        list(snapshots),
        parse_activities(activities, TZ, "UTC"),
        CashYieldConfig(**config),
        start,
        end,
        date.fromisoformat(as_of),
    )


def account_row(month, account_id="acc-a"):
    return next(r for r in month["accounts"] if r["account_id"] == account_id)


def test_balance_is_a_step_function_between_snapshots():
    [month] = run(
        [snap("acc-a", "2026-03-20", 1.0), snap("acc-a", "2026-04-16", 2.0)],
        [interest("i1", "2026-04-20T10:00:00.000Z", 0.01)],
    )["months"]
    row = account_row(month)

    # 15 days at 1.00, then 15 days at 2.00.
    assert row["avg_balance"] == 1.5
    assert row["interest"] == 0.01
    assert row["period_yield"] == pytest.approx(0.01 / 1.5, abs=1e-8)
    assert row["annualized_yield"] == pytest.approx(0.01 / 1.5 * 365 / 30, abs=1e-8)
    assert row["flags"]["partial_coverage"] is False
    assert row["flags"]["max_snapshot_gap_days"] == 27


def test_partly_covered_month_has_no_yield():
    [month] = run(
        [snap("acc-a", "2026-04-11", 1.0)],
        [interest("i1", "2026-04-20T10:00:00.000Z", 0.01)],
    )["months"]
    row = account_row(month)

    assert row["covered_days"] == 20
    assert row["avg_balance"] == 1.0
    assert row["period_yield"] is None
    assert row["annualized_yield"] is None
    assert row["flags"]["partial_coverage"] is True
    assert row["flags"]["history_starts"] == "2026-04-11"


def test_accrual_moves_a_first_of_month_payment_back():
    snapshots = [snap("acc-a", "2026-03-01", 1.0)]
    activities = [interest("i1", "2026-04-30T22:00:00.000Z", 0.01)]  # May 1 local
    paid = run(snapshots, activities, start="2026-04", end="2026-05")
    accrued = run(
        snapshots,
        activities,
        start="2026-04",
        end="2026-05",
        interest_attribution="accrual",
        interest_rules={"acc-a": InterestRule(attribution="previous_month")},
    )

    assert [account_row(m)["interest"] for m in paid["months"]] == [0.0, 0.01]
    assert [account_row(m)["interest"] for m in accrued["months"]] == [0.01, 0.0]


def test_as_of_cuts_the_last_month_and_later_snapshots():
    result = run(
        [snap("acc-a", "2026-03-01", 1.0), snap("acc-a", "2026-04-25", 9.0)],
        start="2026-04",
        end="2026-06",
        as_of="2026-04-20",
    )
    [month] = result["months"]

    assert result["window"]["end_month"] == "2026-04"
    assert month["partial_month"] is True
    assert account_row(month)["days"] == 20
    assert account_row(month)["avg_balance"] == 1.0


def test_total_sums_fully_covered_accounts_and_names_the_rest():
    [month] = run(
        [snap("acc-a", "2026-03-01", 1.0), snap("acc-b", "2026-04-11", 3.0)],
        [
            interest("i1", "2026-04-20T10:00:00.000Z", 0.01),
            interest("i2", "2026-04-20T10:00:00.000Z", 0.02, account="acc-b"),
        ],
        accounts=(A, B),
    )["months"]

    assert month["total"]["avg_balance"] == 1.0
    assert month["total"]["interest"] == 0.01
    assert month["total"]["accounts_left_out"] == ["acc-b"]


def test_interest_on_a_zero_balance_is_flagged():
    [month] = run(
        [snap("acc-a", "2026-03-01", 0.0)],
        [interest("i1", "2026-04-20T10:00:00.000Z", 0.01)],
    )["months"]
    row = account_row(month)

    assert row["period_yield"] is None
    assert row["flags"]["interest_without_balance"] is True


def test_stale_snapshot_is_flagged():
    [month] = run([snap("acc-a", "2026-02-01", 1.0)])["months"]

    assert account_row(month)["flags"]["stale_snapshot"] is True


def test_accounts_without_history_or_interest_are_omitted():
    [month] = run([snap("acc-a", "2026-03-01", 1.0)], accounts=(A, B))["months"]

    assert [r["account_id"] for r in month["accounts"]] == ["acc-a"]


def test_period_summary_spans_all_months():
    result = run(
        [snap("acc-a", "2026-03-01", 1.0), snap("acc-a", "2026-05-01", 2.0)],
        [
            interest("i1", "2026-04-20T10:00:00.000Z", 0.01),
            interest("i2", "2026-05-20T10:00:00.000Z", 0.02),
        ],
        start="2026-04",
        end="2026-05",
    )
    row = account_row(result["summary"])

    assert row["days"] == 61
    assert row["avg_balance"] == pytest.approx((30 * 1.0 + 31 * 2.0) / 61, abs=0.005)
    assert row["interest"] == 0.03
    assert result["method_version"] == METHOD_VERSION


def test_start_after_as_of_is_refused():
    with pytest.raises(ValueError, match="starts after"):
        run([], start="2026-07", end="2026-07", as_of="2026-06-30")


def test_output_is_byte_identical_and_order_independent():
    snapshots = [snap("acc-a", "2026-03-01", 1.0), snap("acc-b", "2026-03-05", 2.0)]
    activities = [
        interest("i1", "2026-04-20T10:00:00.000Z", 0.01),
        interest("i2", "2026-04-21T10:00:00.000Z", 0.02, account="acc-b"),
    ]
    first = run(snapshots, activities, accounts=(A, B))
    second = run(snapshots[::-1], activities[::-1], accounts=(B, A))

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_interest_fees_give_a_net_yield():
    withheld = interest("i1", "2026-04-20T10:00:00.000Z", 0.04)
    withheld["feeInBaseCurrency"] = 0.01
    [month] = run([snap("acc-a", "2026-03-01", 2.0)], [withheld])["months"]
    row = account_row(month)

    assert row["interest"] == 0.04
    assert row["interest_fees"] == 0.01
    assert row["period_yield"] == 0.02
    assert row["net_period_yield"] == 0.015
    assert row["net_annualized_yield"] == pytest.approx(0.015 * 365 / 30, abs=1e-8)
    assert month["total"]["interest_fees"] == 0.01
    assert month["total"]["net_period_yield"] == 0.015
