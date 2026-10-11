"""Tests for the portfolio data audit. All data is synthetic and tiny."""

import json
from datetime import date
from datetime import timedelta

from ghostfolio_mcp.analytics.audit import METHOD_VERSION
from ghostfolio_mcp.analytics.audit import AccountInfo
from ghostfolio_mcp.analytics.audit import AuditConfig
from ghostfolio_mcp.analytics.audit import BalanceSnapshot
from ghostfolio_mcp.analytics.audit import audit_portfolio
from ghostfolio_mcp.analytics.audit import parse_balance_snapshots
from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import parse_activities
from ghostfolio_mcp.analytics.common import parse_chart

TZ = "Europe/Madrid"
AS_OF = date(2026, 8, 31)
SECURITY = "YAHOO:ETF"


def raw(
    id_,
    type_,
    timestamp,
    *,
    quantity=1.0,
    unit_price=1.0,
    fee=0.0,
    amount=None,
    account="acc-a",
    symbol="ETF",
    data_source="YAHOO",
    currency="EUR",
):
    return {
        "id": id_,
        "type": type_,
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": account.upper()},
        "SymbolProfile": {"symbol": symbol, "dataSource": data_source},
        "quantity": quantity,
        "unitPrice": unit_price,
        "fee": fee,
        "feeInBaseCurrency": fee,
        "valueInBaseCurrency": quantity * unit_price if amount is None else amount,
        "currency": currency,
    }


def interest(id_, timestamp, amount=0.01, account="acc-a"):
    return raw(
        id_,
        "INTEREST",
        timestamp,
        quantity=1,
        unit_price=amount,
        amount=amount,
        account=account,
        symbol="GF_INTEREST-CASH",
        data_source="MANUAL",
    )


def prices(*points):
    """(iso date, price[, carried_forward]) tuples -> PricePoint list."""
    return [
        PricePoint(date.fromisoformat(p[0]), p[1], p[2] if len(p) > 2 else False)
        for p in points
    ]


def fresh_prices():
    """A real close just before AS_OF so stale_price stays quiet."""
    return prices((str(AS_OF), 1.0))


def run(
    activities=(),
    *,
    accounts=(),
    snapshots=(),
    price_map=None,
    isins=None,
    chart=(),
    as_of=AS_OF,
    **config,
):
    config.setdefault("timezone", TZ)
    audit_config = AuditConfig(**config)
    parsed = parse_activities(activities, audit_config.timezone, "UTC")
    if price_map is None:
        price_map = {SECURITY: fresh_prices()}
    if isins is None:
        isins = dict.fromkeys(price_map, "XX0000000000")
    return audit_portfolio(
        parsed,
        list(accounts),
        list(snapshots),
        price_map,
        isins,
        parse_chart(chart),
        audit_config,
        as_of,
    )


def checks(result, name):
    return [f for f in result["findings"] if f["check"] == name]


# --- trade dates -------------------------------------------------------------


def test_trade_on_local_date_booked_a_day_early_is_a_warning():
    # Local midnight Mon 2026-03-30 is Sun 03-29 22:00 UTC; Sunday has only a
    # carried-forward close, so the trade can only be Monday's.
    result = run(
        [raw("b1", "BUY", "2026-03-29T22:00:00.000Z", unit_price=1.1)],
        price_map={
            SECURITY: prices(
                ("2026-03-29", 1.0, True), ("2026-03-30", 1.1), (str(AS_OF), 1.0)
            )
        },
    )
    [finding] = checks(result, "trade_date_mismatch")

    assert finding["severity"] == "warn"
    assert finding["evidence"]["verdict"] == "booked_day_before_trade"
    assert finding["dates"] == ["2026-03-29", "2026-03-30"]
    assert finding["activity_ids"] == ["b1"]


def test_trade_on_utc_date_with_a_late_local_date_is_info():
    result = run(
        [raw("b1", "BUY", "2026-07-23T22:00:00.000Z", unit_price=1.001)],
        price_map={
            SECURITY: prices(
                ("2026-07-23", 1.0), ("2026-07-24", 1.05), (str(AS_OF), 1.0)
            )
        },
    )
    [finding] = checks(result, "trade_date_mismatch")

    assert finding["severity"] == "info"
    assert finding["evidence"]["verdict"] == "local_date_after_trade"


def test_price_between_both_closes_is_ambiguous():
    result = run(
        [raw("b1", "BUY", "2026-07-23T22:00:00.000Z", unit_price=1.0005)],
        price_map={
            SECURITY: prices(
                ("2026-07-23", 1.0), ("2026-07-24", 1.001), (str(AS_OF), 1.0)
            )
        },
    )
    [finding] = checks(result, "trade_date_mismatch")

    assert finding["evidence"]["verdict"] == "ambiguous"


def test_intraday_timestamp_has_one_date_and_no_finding():
    result = run(
        [raw("b1", "BUY", "2026-07-23T10:00:00.000Z", unit_price=1.0)],
        price_map={SECURITY: prices(("2026-07-23", 1.0), (str(AS_OF), 1.0))},
    )

    assert checks(result, "trade_date_mismatch") == []


def test_price_far_from_close_is_flagged():
    result = run(
        [raw("b1", "BUY", "2026-07-23T10:00:00.000Z", unit_price=2.0)],
        price_map={SECURITY: prices(("2026-07-23", 1.0), (str(AS_OF), 1.0))},
    )
    [finding] = checks(result, "price_far_from_close")

    assert finding["severity"] == "warn"


# --- duplicates and interest --------------------------------------------------


def test_identical_activities_are_an_error():
    result = run(
        [
            raw("b1", "BUY", "2026-07-23T10:00:00.000Z"),
            raw("b2", "BUY", "2026-07-23T15:00:00.000Z"),
            raw("b3", "BUY", "2026-07-23T15:00:00.000Z", quantity=2.0),
        ]
    )
    [finding] = checks(result, "duplicate_activity")

    assert finding["severity"] == "error"
    assert finding["activity_ids"] == ["b1", "b2"]


def daily_interest(first, last, skip=(), double=()):
    day, end, items = date.fromisoformat(first), date.fromisoformat(last), []
    while day <= end:
        iso = day.isoformat()
        if iso not in skip:
            items.append(interest(f"i-{iso}", f"{iso}T01:00:00.000Z"))
        if iso in double:
            items.append(interest(f"j-{iso}", f"{iso}T01:30:00.000Z", amount=0.02))
        day += timedelta(days=1)
    return items


def test_daily_payer_double_day_next_to_a_missing_day():
    result = run(
        daily_interest(
            "2026-08-01", "2026-08-31", skip=("2026-08-11",), double=("2026-08-10",)
        ),
        interest_rules={"acc-a": InterestRule(expected="daily")},
    )
    [conflict] = checks(result, "interest_day_conflict")
    [missing] = checks(result, "missing_interest_day")

    assert conflict["dates"] == ["2026-08-10", "2026-08-11"]
    assert conflict["activity_ids"] == ["i-2026-08-10", "j-2026-08-10"]
    assert "misdated" in conflict["message"]
    assert missing["dates"] == ["2026-08-11"]


def test_monthly_payer_missing_month():
    result = run(
        [
            interest("t1", "2026-01-31T23:00:00.000Z"),  # Feb 1 local
            interest("t2", "2026-02-28T23:00:00.000Z"),  # Mar 1
            interest("t3", "2026-04-30T22:00:00.000Z"),  # May 1
            interest("t4", "2026-06-30T22:00:00.000Z"),  # Jul 1
            interest("t5", "2026-07-31T22:00:00.000Z"),  # Aug 1
        ],
        interest_rules={"acc-a": InterestRule(expected="monthly")},
    )
    [finding] = checks(result, "missing_interest_month")

    # AS_OF falls in August, so the complete months run to July.
    assert finding["evidence"]["months"] == ["2026-04", "2026-06"]


def test_payers_without_a_rule_are_not_checked_for_cadence():
    result = run(daily_interest("2026-08-01", "2026-08-31", skip=("2026-08-11",)))

    assert checks(result, "missing_interest_day") == []


def test_cash_without_recent_interest_is_info():
    result = run(
        [interest("i1", "2026-01-15T10:00:00.000Z", account="acc-b")],
        accounts=[
            AccountInfo("acc-a", "A", 5.0, excluded_from_analysis=True),
            AccountInfo("acc-b", "B", 5.0),
            AccountInfo("acc-c", "C", 0.5),
        ],
        snapshots=[
            BalanceSnapshot("acc-a", AS_OF, 5.0),
            BalanceSnapshot("acc-b", AS_OF, 5.0),
        ],
        cash_threshold=1.0,
        interest_window_days=30,
    )
    findings = checks(result, "cash_without_interest")

    assert [f["account_id"] for f in findings] == ["acc-a", "acc-b"]
    assert findings[0]["evidence"]["excluded_from_analysis"] is True
    assert findings[1]["evidence"]["last_interest_date"] == "2026-01-15"


# --- staleness ---------------------------------------------------------------


def test_stale_and_missing_balance_snapshots():
    result = run(
        accounts=[
            AccountInfo("acc-a", "A", 5.0),
            AccountInfo("acc-b", "B", 5.0),
            AccountInfo("acc-c", "C", 5.0),
        ],
        snapshots=[
            BalanceSnapshot("acc-a", AS_OF - timedelta(days=10), 5.0),
            BalanceSnapshot("acc-b", AS_OF - timedelta(days=60), 5.0),
        ],
        cash_threshold=1.0,
    )
    findings = checks(result, "stale_balance")

    assert [f["account_id"] for f in findings] == ["acc-b", "acc-c"]
    assert findings[0]["message"] == "Last balance snapshot is 60 days old"
    assert findings[1]["message"] == "No balance snapshot recorded"


def test_stale_price_ignores_carried_forward_closes_and_sold_positions():
    result = run(
        [
            raw("b1", "BUY", "2026-07-01T10:00:00.000Z"),
            raw("b2", "BUY", "2026-07-01T10:00:00.000Z", symbol="OLD"),
            raw("s2", "SELL", "2026-07-02T10:00:00.000Z", symbol="OLD"),
        ],
        price_map={
            SECURITY: prices(("2026-08-20", 1.0), ("2026-08-30", 1.0, True)),
            "YAHOO:OLD": prices(("2026-07-01", 1.0)),
        },
    )
    [finding] = checks(result, "stale_price")

    assert finding["security"] == SECURITY
    assert finding["dates"] == ["2026-08-20"]


# --- chart -------------------------------------------------------------------


def chart_rows(first, count):
    day = date.fromisoformat(first)
    return [
        {
            "date": (day + timedelta(days=i)).isoformat(),
            "netPerformanceWithCurrencyEffect": 0.0,
            "valueWithCurrencyEffect": 1.0,
            "totalInvestmentValueWithCurrencyEffect": 1.0,
        }
        for i in range(count)
    ]


def test_chart_gaps_and_non_sell_invariant_breaks():
    chart = chart_rows("2026-06-30", 62)  # to 2026-08-30
    for row in chart[20:]:
        row["netPerformanceWithCurrencyEffect"] += 0.05  # break on 07-20
    del chart[40]  # 2026-08-09 missing
    result = run(chart=chart)

    [gap] = checks(result, "chart_gap")
    [broken] = checks(result, "invariant_break")
    assert gap["dates"] == ["2026-08-09"]
    assert broken["dates"] == ["2026-07-20"]
    assert broken["evidence"] == {"residual": 0.05, "hint": "unexplained"}


def test_sell_day_breaks_are_not_audit_findings():
    chart = chart_rows("2026-06-30", 62)
    for row in chart[20:]:
        row["netPerformanceWithCurrencyEffect"] += 0.05
    result = run([raw("s1", "SELL", "2026-07-20T10:00:00.000Z")], chart=chart)

    assert checks(result, "invariant_break") == []


# --- portfolio shape ---------------------------------------------------------


def test_tiny_early_buy_is_a_legacy_seed():
    result = run(
        [
            raw("b1", "BUY", "2025-01-02T10:00:00.000Z", unit_price=0.01),
            raw("b2", "BUY", "2025-09-01T10:00:00.000Z", quantity=2.0),
            raw("b3", "BUY", "2025-09-02T10:00:00.000Z", quantity=2.0),
        ]
    )
    [finding] = checks(result, "legacy_seed_position")

    assert finding["activity_ids"] == ["b1"]
    assert finding["dates"] == ["2025-01-02", "2025-09-01"]


def test_mixed_timestamp_styles_per_account_and_kind():
    result = run(
        [
            raw("b1", "BUY", "2026-07-01T10:00:00.000Z"),
            raw("b2", "BUY", "2026-07-01T22:00:00.000Z"),  # local midnight
            raw("b3", "BUY", "2026-07-03T00:00:00.000Z"),  # UTC midnight
            interest("i1", "2026-07-01T10:00:00.000Z"),
        ]
    )
    [finding] = checks(result, "mixed_timestamp_styles")

    assert finding["evidence"] == {
        "kind": "TRADE",
        "styles": {"intraday": 1, "local_midnight": 1, "utc_midnight": 1},
    }


def test_missing_isin_unsupported_types_and_foreign_currency():
    result = run(
        [
            raw("b1", "BUY", "2026-07-01T10:00:00.000Z", currency="USD"),
            raw("d1", "DIVIDEND", "2026-07-02T10:00:00.000Z"),
        ],
        isins={SECURITY: None},
    )

    assert checks(result, "missing_isin")[0]["evidence"] == {"securities": [SECURITY]}
    assert checks(result, "unsupported_activity_type")[0]["activity_ids"] == ["d1"]
    assert checks(result, "non_base_currency")[0]["evidence"] == {
        "currencies": {"USD": 1}
    }


# --- output ------------------------------------------------------------------


def test_activities_after_as_of_are_ignored():
    result = run(
        [
            raw("b1", "BUY", "2026-09-05T10:00:00.000Z"),
            raw("b2", "BUY", "2026-09-05T10:00:00.000Z"),
        ]
    )

    assert checks(result, "duplicate_activity") == []


def test_findings_are_ordered_by_severity_and_summarised():
    result = run(
        [
            raw("b1", "BUY", "2026-07-23T10:00:00.000Z"),
            raw("b2", "BUY", "2026-07-23T10:00:00.000Z"),
            raw("b3", "BUY", "2026-07-24T10:00:00.000Z", unit_price=2.0),
        ],
        price_map={SECURITY: prices(("2026-07-24", 1.0), (str(AS_OF), 1.0))},
        isins={SECURITY: None},
    )

    assert [f["severity"] for f in result["findings"]] == ["error", "warn", "info"]
    assert result["summary"] == {
        "total": 3,
        "by_severity": {"error": 1, "warn": 1, "info": 1},
        "by_check": {
            "duplicate_activity": 1,
            "missing_isin": 1,
            "price_far_from_close": 1,
        },
    }
    assert result["method_version"] == METHOD_VERSION
    assert result["as_of"] == "2026-08-31"


def test_output_is_byte_identical_and_order_independent():
    activities = [
        *daily_interest("2026-08-01", "2026-08-31", skip=("2026-08-11",)),
        raw("b1", "BUY", "2026-07-23T22:00:00.000Z", unit_price=1.001),
        raw("b2", "BUY", "2026-07-23T22:00:00.000Z", unit_price=1.001),
    ]
    kwargs = {
        "price_map": {
            SECURITY: prices(
                ("2026-07-23", 1.0), ("2026-07-24", 1.05), (str(AS_OF), 1.0)
            )
        },
        "interest_rules": {"acc-a": InterestRule(expected="daily")},
    }
    first = run(activities, **kwargs)
    second = run(activities[::-1], **kwargs)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_balance_snapshots_are_parsed_as_dates():
    snapshots = parse_balance_snapshots(
        "acc-a",
        [
            {
                "date": "2026-08-02T00:00:00.000Z",
                "value": 2.0,
                "valueInBaseCurrency": 2.0,
            },
            {"date": "2026-08-01T00:00:00.000Z", "value": 1.0},
        ],
    )

    assert snapshots == [
        BalanceSnapshot("acc-a", date(2026, 8, 1), 1.0),
        BalanceSnapshot("acc-a", date(2026, 8, 2), 2.0),
    ]


# --- refinements from the live run -------------------------------------------


def test_weekend_close_repeating_friday_is_not_a_trading_day():
    # Ghostfolio fills Sat/Sun with Friday's close without marking it carried
    # forward; Sunday 2026-03-29 must still not count as a trading day.
    result = run(
        [raw("b1", "BUY", "2026-03-29T22:00:00.000Z", unit_price=1.0)],
        price_map={
            SECURITY: prices(
                ("2026-03-27", 1.0),
                ("2026-03-28", 1.0),
                ("2026-03-29", 1.0),
                ("2026-03-30", 1.05),
                (str(AS_OF), 1.2),
            )
        },
    )
    [finding] = checks(result, "trade_date_mismatch")

    assert finding["evidence"]["verdict"] == "booked_day_before_trade"
    assert finding["evidence"]["trading_days"] == ["local"]


def test_trades_entered_together_get_one_verdict():
    batch = "2026-07-23T22:00:00.000Z"
    price_map = {
        "YAHOO:A": prices(
            ("2026-07-22", 0.9), ("2026-07-23", 1.0), ("2026-07-24", 1.05)
        ),
        "YAHOO:B": prices(
            ("2026-07-22", 0.9), ("2026-07-23", 1.0), ("2026-07-24", 1.05)
        ),
        "YAHOO:C": prices(
            ("2026-07-22", 0.9), ("2026-07-23", 1.0), ("2026-07-24", 1.01)
        ),
        SECURITY: fresh_prices(),
    }
    result = run(
        [
            raw("b1", "BUY", batch, symbol="A", unit_price=1.001),
            raw("b2", "BUY", batch, symbol="B", unit_price=1.002),
            # On its own this one would point to the local date.
            raw("b3", "BUY", batch, symbol="C", unit_price=1.009),
        ],
        price_map=price_map,
        isins=dict.fromkeys(price_map, "XX0000000000"),
    )
    [finding] = checks(result, "trade_date_mismatch")

    assert finding["activity_ids"] == ["b1", "b2", "b3"]
    assert finding["evidence"]["verdict"] == "local_date_after_trade"
    assert finding["security"] is None
    assert "3 trades entered together" in finding["message"]


def test_an_exact_close_decides_even_when_both_are_near():
    # Fund NAVs: the price equals one day's close exactly; the other day is
    # only 0.1% away, below the 0.2% gap that would otherwise be needed.
    result = run(
        [raw("b1", "BUY", "2026-08-04T22:00:00.000Z", unit_price=1.0)],
        price_map={
            SECURITY: prices(
                ("2026-08-03", 0.9),
                ("2026-08-04", 1.001),
                ("2026-08-05", 1.0),
                (str(AS_OF), 1.2),
            )
        },
    )
    [finding] = checks(result, "trade_date_mismatch")

    assert finding["evidence"]["verdict"] == "booked_day_before_trade"
    assert "is this day's close" in finding["message"]


def test_daily_cadence_is_judged_per_kind_of_interest():
    cash = daily_interest("2026-08-01", "2026-08-31", skip=("2026-08-11",))
    lending = [
        {
            **interest(f"l{d}", f"2026-08-{d:02d}T03:00:00.000Z"),
            "SymbolProfile": {"symbol": "GF_INTEREST-LENDING", "dataSource": "MANUAL"},
        }
        for d in (3, 11, 20)  # occasional, and present on the missing cash day
    ]
    result = run(
        [*cash, *lending], interest_rules={"acc-a": InterestRule(expected="daily")}
    )
    [missing] = checks(result, "missing_interest_day")

    assert missing["security"] == "MANUAL:GF_INTEREST-CASH"
    assert missing["dates"] == ["2026-08-11"]


def test_stale_price_ignores_repeated_closes():
    result = run(
        [raw("b1", "BUY", "2026-07-01T10:00:00.000Z")],
        price_map={
            SECURITY: prices(
                ("2026-08-19", 0.9),
                ("2026-08-20", 1.0),
                *((f"2026-08-{d}", 1.0) for d in range(21, 32)),
            )
        },
    )
    [finding] = checks(result, "stale_price")

    assert finding["dates"] == ["2026-08-20"]


def test_accounts_configured_as_paying_no_interest_are_not_flagged():
    result = run(
        accounts=[AccountInfo("acc-a", "A", 5.0), AccountInfo("acc-b", "B", 5.0)],
        snapshots=[
            BalanceSnapshot("acc-a", AS_OF, 5.0),
            BalanceSnapshot("acc-b", AS_OF, 5.0),
        ],
        cash_threshold=1.0,
        no_interest_accounts={"acc-a"},
    )

    assert [f["account_id"] for f in checks(result, "cash_without_interest")] == [
        "acc-b"
    ]
    assert result["config"]["no_interest_accounts"] == ["acc-a"]
