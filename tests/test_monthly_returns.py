"""Unit tests for the pure monthly returns module.

All data is synthetic and deliberately tiny. The helpers build chart rows the
way Ghostfolio does: value excludes cash, netPerformance is net of fees and
excludes interest, so on an ordinary day dNP == dV - dInvestment - fees.
"""

import json
from datetime import date
from datetime import timedelta

import pytest

from ghostfolio_mcp.analytics.monthly_returns import METHOD_VERSION
from ghostfolio_mcp.analytics.monthly_returns import InterestRule
from ghostfolio_mcp.analytics.monthly_returns import ReturnsConfig
from ghostfolio_mcp.analytics.monthly_returns import compute_monthly_returns
from ghostfolio_mcp.analytics.monthly_returns import parse_activities
from ghostfolio_mcp.analytics.monthly_returns import parse_chart

TZ = "Europe/Madrid"


def chart_row(day, net_performance, value, investment):
    return {
        "date": day,
        "netPerformance": net_performance,
        "netPerformanceWithCurrencyEffect": net_performance,
        "value": value,
        "valueWithCurrencyEffect": value,
        "totalInvestment": investment,
        "totalInvestmentValueWithCurrencyEffect": investment,
    }


def build_chart(start, moves, value=1.0, investment=1.0):
    """Daily rows from `start`; each move is (pnl, flow_at_cost, fee)."""
    day = date.fromisoformat(start)
    net_performance = 0.0
    rows = [chart_row(day.isoformat(), net_performance, value, investment)]
    for pnl, flow, fee in moves:
        day += timedelta(days=1)
        net_performance += pnl
        investment += flow
        # The fee leaves the investor's pocket into the sleeve and is lost at
        # once: it is in the flow, and dNP already has it subtracted.
        value += pnl + flow + fee
        rows.append(chart_row(day.isoformat(), net_performance, value, investment))
    return rows


def activity(
    id_,
    type_,
    timestamp,
    amount=0.0,
    fee=0.0,
    account="acc-a",
    name="Broker A",
):
    return {
        "id": id_,
        "type": type_,
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": name},
        "SymbolProfile": {"symbol": "GF_INTEREST-CASH" if type_ == "INTEREST" else "X"},
        "valueInBaseCurrency": amount,
        "feeInBaseCurrency": fee,
    }


def run(chart, activities=(), start="2026-04", end="2026-04", **config):
    config.setdefault("timezone", TZ)
    returns_config = ReturnsConfig(**config)
    return compute_monthly_returns(
        parse_chart(chart),
        parse_activities(
            activities, returns_config.timezone, returns_config.chart_timezone
        ),
        returns_config,
        start,
        end,
    )


def flat_days(count):
    return [(0.0, 0.0, 0.0)] * count


# --- timezone bucketing -------------------------------------------------------


@pytest.mark.parametrize(
    ("timestamp", "expected_month"),
    [
        # Local midnight on the 1st, stored as UTC the evening before.
        ("2026-03-31T22:00:00.000Z", "2026-04"),  # CEST, UTC+2
        ("2026-02-28T23:00:00.000Z", "2026-03"),  # CET, UTC+1
        ("2026-10-31T23:00:00.000Z", "2026-11"),  # CET again after DST ends
        # The last UTC hour that is still the previous local month.
        ("2026-03-31T21:59:59.000Z", "2026-03"),
        ("2026-10-31T22:59:59.000Z", "2026-10"),
    ],
)
def test_activity_month_uses_local_timezone(timestamp, expected_month):
    [parsed] = parse_activities(
        [activity("i1", "INTEREST", timestamp, amount=0.01)], TZ, "UTC"
    )

    assert parsed.local_date.strftime("%Y-%m") == expected_month


def test_interest_at_local_month_edge_is_booked_in_the_local_month():
    chart = build_chart("2026-02-28", flat_days(61))
    result = run(
        chart,
        [activity("i1", "INTEREST", "2026-03-31T22:00:00.000Z", amount=0.05)],
        start="2026-03",
        end="2026-04",
    )
    march, april = result["months"]

    assert march["interest"] == 0.0
    assert april["interest"] == 0.05
    assert april["interest_by_account"] == [
        {"account_id": "acc-a", "account_name": "Broker A", "interest": 0.05}
    ]


def test_fee_follows_the_chart_calendar_and_mismatch_is_reported():
    # A buy entered as local midnight on Apr 1 is priced by Ghostfolio (UTC)
    # on Mar 31, so its flow and fee sit in March's chart delta.
    moves = flat_days(61)
    moves[30] = (-0.03, 2.0, 0.03)  # chart day 2026-03-31
    chart = build_chart("2026-02-28", moves)
    result = run(
        chart,
        [activity("b1", "BUY", "2026-03-31T22:00:00.000Z", amount=2.0, fee=0.03)],
        start="2026-03",
        end="2026-04",
    )
    march, april = result["months"]

    assert march["fees"] == 0.03
    assert march["market_pnl_net"] == -0.03
    assert march["market_pnl_gross"] == 0.0
    assert march["flags"]["invariant_breaks"] == []
    assert march["flags"]["calendar_mismatches"] == [
        {
            "activity_id": "b1",
            "type": "BUY",
            "local_date": "2026-04-01",
            "chart_date": "2026-03-31",
        }
    ]
    assert april["fees"] == 0.0


# --- invariant ---------------------------------------------------------------


def test_fee_days_do_not_break_the_invariant():
    moves = flat_days(30)
    moves[4] = (-0.03, 1.0, 0.03)
    result = run(
        build_chart("2026-03-31", moves),
        [activity("b1", "BUY", "2026-04-05T09:00:00.000Z", amount=1.0, fee=0.03)],
    )

    assert result["months"][0]["flags"]["invariant_breaks"] == []


def test_sell_day_break_is_reported_not_corrected():
    # Sold 1.00 of value at cost 0.80; the closing price the chart uses
    # differs from execution, so dNP != dV - dInvestment by 0.02.
    chart = build_chart("2026-03-31", flat_days(30), value=2.0, investment=1.6)
    for row in chart[10:]:
        row["valueWithCurrencyEffect"] -= 1.0
        row["totalInvestmentValueWithCurrencyEffect"] -= 0.8
        row["netPerformanceWithCurrencyEffect"] -= 0.18
    [month] = run(
        chart, [activity("s1", "SELL", "2026-04-10T10:00:00.000Z", amount=1.0)]
    )["months"]

    assert month["flags"]["sell_days"] == ["2026-04-10"]
    assert month["flags"]["invariant_breaks"] == [
        {"date": "2026-04-10", "residual": 0.02, "hint": "sell_day"}
    ]
    assert month["market_pnl_net"] == -0.18
    assert month["net_flows"] == -0.8


def test_interest_leaking_into_net_performance_is_caught():
    # A future Ghostfolio counting interest in netPerformance would raise dNP
    # by the interest without moving value (value excludes cash).
    chart = build_chart("2026-03-31", flat_days(30))
    for row in chart[15:]:
        row["netPerformanceWithCurrencyEffect"] += 0.07
    [month] = run(
        chart, [activity("i1", "INTEREST", "2026-04-15T10:00:00.000Z", amount=0.07)]
    )["months"]

    assert month["flags"]["invariant_breaks"] == [
        {"date": "2026-04-15", "residual": 0.07, "hint": "matches_interest"}
    ]


# --- returns -----------------------------------------------------------------


def test_zero_flow_month_twr_equals_modified_dietz():
    moves = [(0.01 * ((i % 5) - 2), 0.0, 0.0) for i in range(30)]
    [month] = run(build_chart("2026-03-31", moves, value=3.0))["months"]

    assert month["net_flows"] == 0.0
    assert month["avg_invested_capital"] == 3.0
    assert month["twr"] == pytest.approx(month["modified_dietz"], abs=1e-8)
    assert month["modified_dietz"] == pytest.approx(
        month["market_pnl_net"] / 3.0, abs=1e-8
    )


def test_flows_are_at_start_of_day_and_weighted_by_remaining_days():
    # 1.00 invested on day 16 of 30 at start of day: weight (30-16+1)/30.
    moves = flat_days(30)
    moves[15] = (0.1, 1.0, 0.0)
    [month] = run(build_chart("2026-03-31", moves))["months"]

    assert month["avg_invested_capital"] == pytest.approx(1.0 + 15 / 30)
    assert month["modified_dietz"] == pytest.approx(0.1 / 1.5, abs=1e-8)
    # Start-of-day flow: that day's return is 0.1 on capital 1 + 1.
    assert month["twr"] == pytest.approx(0.05, abs=1e-8)


def test_sell_is_a_negative_flow_that_shrinks_capital():
    moves = flat_days(30)
    moves[9] = (0.0, -1.0, 0.0)  # sell 1.00 at start of day 10
    moves[19] = (0.1, 0.0, 0.0)  # then earn 0.10 on the remaining 1.00
    [month] = run(build_chart("2026-03-31", moves, value=2.0, investment=2.0))["months"]

    assert month["net_flows"] == -1.0
    assert month["twr"] == pytest.approx(0.1, abs=1e-8)
    assert month["avg_invested_capital"] == pytest.approx(2.0 - 21 / 30)


def test_earning_on_zero_capital_makes_twr_undefined():
    moves = flat_days(30)
    moves[4] = (0.0, -1.0, 0.0)  # sell everything
    moves[6] = (0.05, 0.0, 0.0)  # P&L with nothing invested
    chart = build_chart("2026-03-31", moves)
    # Keep value at zero after the sale even though dNP moved.
    for row in chart[7:]:
        row["valueWithCurrencyEffect"] = 0.0
    [month] = run(chart)["months"]

    assert month["twr"] is None
    assert month["flags"]["undefined_return_days"] == ["2026-04-07"]


def test_empty_portfolio_days_are_flat_not_undefined():
    chart = build_chart("2026-03-31", flat_days(30), value=0.0, investment=0.0)
    [month] = run(chart)["months"]

    assert month["twr"] == 0.0
    assert month["modified_dietz"] is None
    assert month["flags"]["undefined_return_days"] == []


# --- window and flags --------------------------------------------------------


def test_partial_month_is_flagged():
    [month] = run(build_chart("2026-03-31", flat_days(12)))["months"]

    assert month["flags"]["partial_month"] is True


def test_full_month_is_not_partial():
    [month] = run(build_chart("2026-03-31", flat_days(30)))["months"]

    assert month["flags"]["partial_month"] is False


def test_requires_a_row_before_start_month():
    with pytest.raises(ValueError, match="no row before 2026-04-01"):
        run(build_chart("2026-04-01", flat_days(29)))


def test_end_month_is_clipped_to_the_data():
    result = run(build_chart("2026-03-31", flat_days(40)), end="2026-06")

    assert [m["month"] for m in result["months"]] == ["2026-04", "2026-05"]
    assert result["window"]["end_month"] == "2026-05"
    assert result["summary"]["flags"]["partial_period"] is True


def test_gap_inside_a_month_is_flagged_and_attributed_to_the_next_row():
    chart = build_chart("2026-03-31", [(0.01, 0.0, 0.0)] * 30)
    del chart[10]  # 2026-04-10 missing
    [month] = run(chart)["months"]

    assert month["flags"]["chart_gaps"] == ["2026-04-10"]
    assert month["flags"]["boundary_gap"] is False
    assert month["market_pnl_net"] == 0.3


def test_gap_across_a_month_boundary_is_flagged_on_both_months():
    # With Mar 31 missing, the Mar 30 -> Apr 1 delta mixes both months.
    chart = build_chart("2026-02-28", flat_days(61))
    del chart[31]  # 2026-03-31 missing
    march, april = run(chart, start="2026-03", end="2026-04")["months"]

    assert march["flags"]["boundary_gap"] is True
    assert april["flags"]["boundary_gap"] is True
    assert march["flags"]["chart_gaps"] == ["2026-03-31"]


def test_gap_on_the_first_of_a_month_stays_inside_that_month():
    chart = build_chart("2026-02-28", flat_days(61))
    del chart[32]  # 2026-04-01 missing: the Mar 31 -> Apr 2 delta is all April
    march, april = run(chart, start="2026-03", end="2026-04")["months"]

    assert march["flags"]["boundary_gap"] is False
    assert april["flags"]["boundary_gap"] is False
    assert april["flags"]["chart_gaps"] == ["2026-04-01"]


def test_last_complete_date_drops_the_live_row():
    rows = parse_chart(build_chart("2026-03-31", [(0.01, 0.0, 0.0)] * 30))
    result = compute_monthly_returns(
        rows, [], ReturnsConfig(), "2026-04", "2026-04", date(2026, 4, 29)
    )

    assert result["window"]["dropped_chart_dates"] == ["2026-04-30"]
    assert result["months"][0]["market_pnl_net"] == 0.29
    assert result["months"][0]["flags"]["partial_month"] is True


def test_unsupported_activity_types_are_counted():
    result = run(
        build_chart("2026-03-31", flat_days(30)),
        [activity("d1", "DIVIDEND", "2026-04-03T10:00:00.000Z", amount=0.02)],
    )

    assert result["summary"]["flags"]["unsupported_activities"] == {"DIVIDEND": 1}


# --- interest attribution ----------------------------------------------------


def interest_fixture():
    chart = build_chart("2026-03-31", flat_days(61))  # through 2026-05-31
    activities = [
        # Pays on the 1st for the month just ended.
        activity("m1", "INTEREST", "2026-04-30T22:00:00.000Z", 0.04, account="acc-m"),
        activity("m2", "INTEREST", "2026-05-31T22:00:00.000Z", 0.05, account="acc-m"),
        # Pays daily.
        activity("d1", "INTEREST", "2026-04-15T01:00:00.000Z", 0.01, account="acc-d"),
        activity("d2", "INTEREST", "2026-05-15T01:00:00.000Z", 0.02, account="acc-d"),
    ]
    rules = {
        "acc-m": InterestRule(attribution="previous_month", expected="monthly"),
        "acc-d": InterestRule(expected="monthly"),
    }
    return chart, activities, rules


def test_payment_date_attribution_ignores_rules():
    chart, activities, rules = interest_fixture()
    april, may = run(chart, activities, end="2026-05", interest_rules=rules)["months"]

    assert april["interest"] == 0.01
    assert may["interest"] == 0.06
    assert april["flags"]["missing_interest_accounts"] == ["acc-m"]


def test_accrual_moves_previous_month_payers_back_one_month():
    chart, activities, rules = interest_fixture()
    april, may = run(
        chart,
        activities,
        end="2026-05",
        interest_attribution="accrual",
        interest_rules=rules,
    )["months"]

    assert april["interest"] == 0.05
    assert may["interest"] == 0.02
    # May's accrual is only paid in June, after the data ends.
    assert may["flags"]["missing_interest_accounts"] == ["acc-m"]
    assert april["flags"]["missing_interest_accounts"] == []


def test_accrual_without_a_rule_keeps_payment_date():
    chart, activities, _rules = interest_fixture()
    april, may = run(chart, activities, end="2026-05", interest_attribution="accrual")[
        "months"
    ]

    assert april["interest"] == 0.01
    assert may["interest"] == 0.06


# --- output ------------------------------------------------------------------


def test_totals_and_envelope():
    moves = flat_days(30)
    moves[4] = (-0.03, 1.0, 0.03)
    moves[20] = (0.2, 0.0, 0.0)
    result = run(
        build_chart("2026-03-31", moves),
        [
            activity("b1", "BUY", "2026-04-05T09:00:00.000Z", amount=1.0, fee=0.03),
            activity("i1", "INTEREST", "2026-04-20T09:00:00.000Z", amount=0.04),
        ],
    )
    [month] = result["months"]

    assert result["method_version"] == METHOD_VERSION
    assert result["config"]["timezone"] == TZ
    assert result["window"]["baseline_date"] == "2026-03-31"
    assert month["market_pnl_net"] == 0.17
    assert month["fees"] == 0.03
    assert month["market_pnl_gross"] == 0.2
    assert month["total_gross"] == 0.24
    assert month["net_flows"] == 1.0
    assert result["summary"]["total_gross"] == 0.24


def test_rounding_happens_only_at_the_output():
    # Thirty 0.001 moves sum to 0.03; rounding each day first would give 0.
    [month] = run(build_chart("2026-03-31", [(0.001, 0.0, 0.0)] * 30))["months"]

    assert month["market_pnl_net"] == 0.03


def test_output_is_byte_identical_and_order_independent():
    chart, activities, rules = interest_fixture()
    first = run(chart, activities, end="2026-05", interest_rules=rules)

    second = run(chart[::-1], activities[::-1], end="2026-05", interest_rules=rules)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
