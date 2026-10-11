"""Tests for FIFO realized gains. All data is synthetic and tiny."""

import json
from datetime import date

import pytest

from ghostfolio_mcp.analytics.common import PricePoint
from ghostfolio_mcp.analytics.common import parse_activities
from ghostfolio_mcp.analytics.realized_gains import METHOD_VERSION
from ghostfolio_mcp.analytics.realized_gains import GainsConfig
from ghostfolio_mcp.analytics.realized_gains import add_months
from ghostfolio_mcp.analytics.realized_gains import compute_realized_gains

TZ = "Europe/Madrid"


def trade(
    id_,
    type_,
    timestamp,
    qty,
    price,
    fee=0.0,
    account="acc-a",
    symbol="ETF",
    currency="EUR",
):
    return {
        "id": id_,
        "type": type_,
        "date": timestamp,
        "accountId": account,
        "account": {"id": account, "name": account.upper()},
        "SymbolProfile": {"symbol": symbol, "dataSource": "YAHOO"},
        "quantity": qty,
        "unitPrice": price,
        "fee": fee,
        "feeInBaseCurrency": fee,
        "valueInBaseCurrency": qty * price,
        "currency": currency,
    }


def run(
    activities,
    year=None,
    prices=None,
    as_of=None,
    include_open_lot_detail=False,
    **config,
):
    config.setdefault("timezone", TZ)
    return compute_realized_gains(
        parse_activities(activities, TZ, "UTC"),
        GainsConfig(**config),
        year,
        prices=prices,
        as_of=as_of,
        include_open_lot_detail=include_open_lot_detail,
    )


def test_fifo_across_accounts_with_fees():
    result = run(
        [
            trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0, fee=0.01),
            trade("b2", "BUY", "2026-02-10T10:00:00Z", 1, 2.0, account="acc-b"),
            trade("s1", "SELL", "2026-06-10T10:00:00Z", 1.5, 3.0, fee=0.03),
        ]
    )
    [sale] = result["sales"]

    assert sale["proceeds"] == 4.47
    assert sale["cost"] == 2.01  # 1.00 + 0.01 fee, plus half of 2.00
    assert sale["gain"] == 2.46
    assert [
        (lot["buy_activity_id"], lot["account_id"], lot["quantity"])
        for lot in sale["lots"]
    ] == [
        ("b1", "acc-a", 1.0),
        ("b2", "acc-b", 0.5),
    ]
    assert [lot["holding_days"] for lot in sale["lots"]] == [151, 120]
    assert result["open_lots"] == [
        {
            "security": "YAHOO:ETF",
            "quantity": 0.5,
            "cost": 1.0,
            "lots": 1,
            # Not valued: no prices were given.
            "market_price": None,
            "price_date": None,
            "market_value": None,
            "unrealized_gain": None,
            "unrealized_pct": None,
        }
    ]


def test_excluded_accounts_are_left_out():
    result = run(
        [
            trade("p1", "BUY", "2026-01-10T10:00:00Z", 1, 0.5, account="pension"),
            trade("b1", "BUY", "2026-02-10T10:00:00Z", 1, 1.0),
            trade("s1", "SELL", "2026-06-10T10:00:00Z", 1, 2.0),
            trade("p2", "SELL", "2026-06-11T10:00:00Z", 1, 2.0, account="pension"),
        ],
        excluded_accounts={"pension"},
    )

    assert [s["activity_id"] for s in result["sales"]] == ["s1"]
    assert result["sales"][0]["cost"] == 1.0


def test_buy_before_sell_on_an_identical_timestamp():
    [sale] = run(
        [
            trade("s1", "SELL", "2026-03-10T10:00:00Z", 1, 2.0),
            trade("b1", "BUY", "2026-03-10T10:00:00Z", 1, 1.0),
        ]
    )["sales"]

    assert sale["gain"] == 1.0


def test_selling_more_than_held_reports_the_uncovered_quantity():
    [sale] = run(
        [
            trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0),
            trade("s1", "SELL", "2026-02-10T10:00:00Z", 1.5, 2.0),
        ]
    )["sales"]

    assert sale["gain"] is None
    assert sale["flags"]["uncovered_quantity"] == 0.5
    assert sale["cost"] == 1.0


def test_loss_with_a_repurchase_within_two_months_is_flagged():
    [sale] = run(
        [
            trade("b1", "BUY", "2026-01-10T10:00:00Z", 2, 2.0),
            trade("s1", "SELL", "2026-03-10T10:00:00Z", 2, 1.0),
            trade("b2", "BUY", "2026-04-20T10:00:00Z", 1, 1.0),  # within 2 months
            trade("b3", "BUY", "2026-06-20T10:00:00Z", 1, 1.0),  # outside
        ]
    )["sales"]
    wash = sale["flags"]["wash_sale"]

    assert sale["gain"] == -2.0
    assert wash["window"] == ["2026-01-10", "2026-05-10"]
    assert wash["affected_quantity"] == 1.0
    assert wash["affected_share"] == 0.5
    assert wash["affected_loss"] == -1.0
    # b1 is inside the window but this sale used it up, so it isn't held.
    assert [b["activity_id"] for b in wash["buys"]] == ["b2"]


def test_shares_left_from_an_earlier_buy_count_for_the_two_month_rule():
    [sale] = run(
        [
            trade("b1", "BUY", "2026-02-10T10:00:00Z", 3, 2.0),
            trade("s1", "SELL", "2026-03-10T10:00:00Z", 1, 1.0),
        ]
    )["sales"]

    assert sale["flags"]["wash_sale"]["buys"] == [
        {
            "activity_id": "b1",
            "date": "2026-02-10",
            "account_id": "acc-a",
            "security": "YAHOO:ETF",
            "quantity_held": 2.0,
        }
    ]
    assert sale["flags"]["wash_sale"]["affected_quantity"] == 1.0


def test_gains_and_disabled_rule_are_never_flagged():
    activities = [
        trade("b1", "BUY", "2026-01-10T10:00:00Z", 2, 2.0),
        trade("s1", "SELL", "2026-03-10T10:00:00Z", 1, 3.0),
        trade("s2", "SELL", "2026-03-11T10:00:00Z", 1, 1.0),
    ]

    gain, loss = run(activities)["sales"]
    assert gain["flags"]["wash_sale"] is None
    assert loss["flags"]["wash_sale"] is None  # nothing left held after it

    off = run(
        [
            *activities[:2],
            trade("b2", "BUY", "2026-03-20T10:00:00Z", 1, 1.0),
            activities[2],
        ],
        wash_sale_months=0,
    )
    assert all(s["flags"]["wash_sale"] is None for s in off["sales"])


def test_year_selects_sales_but_lots_use_all_history():
    result = run(
        [
            trade("b1", "BUY", "2025-01-10T10:00:00Z", 2, 1.0),
            trade("s1", "SELL", "2025-06-10T10:00:00Z", 1, 3.0),
            trade("s2", "SELL", "2026-06-10T10:00:00Z", 1, 0.5),
        ],
        year=2026,
    )

    assert [s["activity_id"] for s in result["sales"]] == ["s2"]
    assert result["sales"][0]["cost"] == 1.0
    assert list(result["by_year"]) == ["2026"]


def test_tax_year_is_the_local_date():
    # 23:30 UTC on Dec 31 is already Jan 1 in Madrid.
    result = run(
        [
            trade("b1", "BUY", "2025-06-10T10:00:00Z", 1, 1.0),
            trade("s1", "SELL", "2025-12-31T23:30:00Z", 1, 2.0),
        ]
    )

    assert result["sales"][0]["tax_year"] == 2026


def test_yearly_summary():
    result = run(
        [
            # Inside the two-month window of s2, with one share still held after it.
            trade("b1", "BUY", "2026-02-10T10:00:00Z", 3, 2.0),
            trade("s1", "SELL", "2026-03-10T10:00:00Z", 1, 3.0),
            trade("s2", "SELL", "2026-03-11T10:00:00Z", 1, 1.5),
        ]
    )

    assert result["by_year"]["2026"] == {
        "sales": 2,
        "gains": 1.0,
        "losses": -0.5,
        "net": 0.5,
        "proceeds": 4.5,
        "losses_flagged_two_month_rule": -0.5,
        "sales_with_uncovered_quantity": 0,
    }
    assert result["method_version"] == METHOD_VERSION


def test_trades_outside_the_base_currency_are_refused():
    with pytest.raises(ValueError, match="not in EUR"):
        run([trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 1.0, currency="USD")])


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (date(2026, 3, 31), -1, date(2026, 2, 28)),
        (date(2026, 12, 31), 2, date(2027, 2, 28)),
        (date(2028, 4, 30), -2, date(2028, 2, 29)),
        (date(2026, 1, 15), -2, date(2025, 11, 15)),
    ],
)
def test_add_months_clamps_to_month_end(day, months, expected):
    assert add_months(day, months) == expected


def test_output_is_byte_identical_and_order_independent():
    activities = [
        trade("b1", "BUY", "2026-01-10T10:00:00Z", 2, 2.0),
        trade("b2", "BUY", "2026-01-11T10:00:00Z", 1, 1.0, account="acc-b"),
        trade("s1", "SELL", "2026-03-10T10:00:00Z", 2.5, 1.0),
    ]

    first = run(activities)
    second = run(activities[::-1])

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --- realized-gains/2 --------------------------------------------------------


def point(day, price):
    return PricePoint(date.fromisoformat(day), price)


def open_position():
    return [
        trade("b1", "BUY", "2026-01-10T10:00:00Z", 2, 1.0, fee=0.02),
        trade("b2", "BUY", "2026-03-10T10:00:00Z", 1, 1.5, account="acc-b"),
        trade("s1", "SELL", "2026-04-10T10:00:00Z", 1, 2.0),
    ]


def test_open_lots_are_valued_at_the_last_close_on_or_before_as_of():
    result = run(
        open_position(),
        prices={"YAHOO:ETF": [point("2026-08-28", 2.0), point("2026-09-01", 9.0)]},
        as_of=date(2026, 8, 31),
    )
    [lot] = result["open_lots"]

    # Left: 1 share of b1 (cost 1.01 incl. half the fee) + 1 of b2 (1.50).
    assert lot["quantity"] == 2.0
    assert lot["cost"] == 2.51
    assert lot["market_price"] == 2.0
    assert lot["price_date"] == "2026-08-28"
    assert lot["market_value"] == 4.0
    assert lot["unrealized_gain"] == 1.49
    assert lot["unrealized_pct"] == pytest.approx(1.49 / 2.51, abs=1e-8)
    assert "lot_detail" not in lot
    assert result["valuation_date"] == "2026-08-31"


def test_open_lot_detail_is_in_fifo_order_with_prorated_fees():
    result = run(
        open_position(),
        prices={"YAHOO:ETF": [point("2026-08-31", 2.0)]},
        as_of=date(2026, 8, 31),
        include_open_lot_detail=True,
    )
    [lot] = result["open_lots"]

    assert lot["lot_detail"] == [
        {
            "buy_activity_id": "b1",
            "buy_date": "2026-01-10",
            "account_id": "acc-a",
            "quantity": 1.0,
            "cost": 1.01,
            "holding_days": 233,
            "market_value": 2.0,
            "unrealized_gain": 0.99,
            "unrealized_pct": pytest.approx(0.99 / 1.01, abs=1e-8),
        },
        {
            "buy_activity_id": "b2",
            "buy_date": "2026-03-10",
            "account_id": "acc-b",
            "quantity": 1.0,
            "cost": 1.5,
            "holding_days": 174,
            "market_value": 2.0,
            "unrealized_gain": 0.5,
            "unrealized_pct": pytest.approx(0.5 / 1.5, abs=1e-8),
        },
    ]
    assert result["config"]["include_open_lot_detail"] is True


def test_homogeneous_group_extends_the_two_month_rule():
    activities = [
        trade("b1", "BUY", "2026-01-10T10:00:00Z", 1, 2.0, symbol="ETF"),
        trade("s1", "SELL", "2026-03-10T10:00:00Z", 1, 1.0, symbol="ETF"),
        # Same index, other listing, bought within two months.
        trade("b2", "BUY", "2026-04-01T10:00:00Z", 1, 1.0, symbol="ETF2"),
    ]

    plain = run(activities)["sales"][0]
    grouped = run(activities, homogeneous_groups=[["YAHOO:ETF", "YAHOO:ETF2"]])

    assert plain["flags"]["wash_sale"] is None
    wash = grouped["sales"][0]["flags"]["wash_sale"]
    assert wash["buys"] == [
        {
            "activity_id": "b2",
            "date": "2026-04-01",
            "account_id": "acc-a",
            "security": "YAHOO:ETF2",
            "quantity_held": 1.0,
        }
    ]
    assert grouped["config"]["homogeneous_groups"] == [["YAHOO:ETF", "YAHOO:ETF2"]]
    # Lots are still matched per asset.
    assert grouped["sales"][0]["cost"] == 2.0
