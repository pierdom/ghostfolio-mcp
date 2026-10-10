"""Tests for the import dry run. All data is synthetic and tiny."""

import json
from datetime import date

import pytest

from ghostfolio_mcp.analytics.common import InterestRule
from ghostfolio_mcp.analytics.common import parse_activities
from ghostfolio_mcp.analytics.import_preview import METHOD_VERSION
from ghostfolio_mcp.analytics.import_preview import PreviewConfig
from ghostfolio_mcp.analytics.import_preview import preview_import

TZ = "Europe/Madrid"
TODAY = date(2026, 8, 31)
ACCOUNTS = {"acc-a": "A", "acc-b": "B"}


def proposed(**overrides):
    item = {
        "accountId": "acc-a",
        "currency": "EUR",
        "dataSource": "YAHOO",
        "date": "2026-08-10T10:00:00.000Z",
        "fee": 0.01,
        "quantity": 1,
        "symbol": "ETF",
        "type": "BUY",
        "unitPrice": 1.5,
    }
    item.update(overrides)
    return {k: v for k, v in item.items() if v is not None}


def existing(id_, **overrides):
    item = proposed(**overrides)
    profile = {"symbol": item.pop("symbol"), "dataSource": item.pop("dataSource")}
    return {
        "id": id_,
        "account": {"id": item["accountId"], "name": ACCOUNTS[item["accountId"]]},
        "SymbolProfile": profile,
        "valueInBaseCurrency": item["quantity"] * item["unitPrice"],
        "feeInBaseCurrency": item.get("fee", 0),
        **item,
    }


def run(items, current=(), **config):
    config.setdefault("timezone", TZ)
    return preview_import(
        items,
        parse_activities(current, TZ, "UTC"),
        ACCOUNTS,
        PreviewConfig(**config),
        TODAY,
    )


def test_identical_activity_is_a_duplicate():
    result = run([proposed()], [existing("e1")])
    [item] = result["items"]

    assert item["status"] == "duplicate"
    assert item["matches"] == ["e1"]


def test_same_local_day_at_another_time_is_still_a_duplicate():
    [item] = run([proposed(date="2026-08-10T16:30:00.000Z")], [existing("e1")])["items"]

    assert item["status"] == "duplicate"


def test_float_noise_does_not_hide_a_duplicate():
    [item] = run([proposed(unitPrice=1.5000000001)], [existing("e1")])["items"]

    assert item["status"] == "duplicate"


def test_different_quantity_on_the_same_day_is_a_conflict():
    [item] = run([proposed(quantity=2)], [existing("e1")])["items"]

    assert item["status"] == "conflict"
    assert item["matches"] == ["e1"]


def test_other_account_symbol_type_or_day_is_new():
    items = [
        proposed(accountId="acc-b"),
        proposed(symbol="OTHER"),
        proposed(type="SELL"),
        proposed(date="2026-08-11T10:00:00.000Z"),
    ]

    result = run(items, [existing("e1")])

    assert [i["status"] for i in result["items"]] == ["new"] * 4


def test_days_are_compared_in_the_local_timezone():
    # 22:30 UTC on Aug 9 is Aug 10 in Madrid: the same day as the existing one.
    [item] = run([proposed(date="2026-08-09T22:30:00.000Z")], [existing("e1")])["items"]

    assert item["status"] == "duplicate"
    assert item["local_date"] == "2026-08-10"
    assert item["booked_date"] == "2026-08-09"


def test_duplicates_inside_the_batch_are_caught():
    result = run([proposed(), proposed(), proposed(quantity=3)])

    assert [i["status"] for i in result["items"]] == ["new", "duplicate", "conflict"]
    assert result["items"][1]["matches"] == ["batch:0"]
    assert result["items"][2]["matches"] == ["batch:0", "batch:1"]


def interest_item(amount, timestamp="2026-08-10T01:00:00.000Z"):
    return proposed(
        type="INTEREST",
        dataSource="MANUAL",
        symbol="GF_INTEREST-CASH",
        quantity=1,
        unitPrice=amount,
        fee=0,
        date=timestamp,
    )


def test_second_interest_payment_of_a_daily_payer_is_a_conflict():
    current = [existing("e1", **interest_item(0.01))]

    [item] = run(
        [interest_item(0.02, "2026-08-10T05:00:00.000Z")],
        current,
        interest_rules={"acc-a": InterestRule(expected="daily")},
    )["items"]

    assert item["status"] == "conflict"
    assert "daily interest payer" in item["reasons"][0]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"symbol": None}, "missing symbol"),
        ({"type": "GIFT"}, "unknown type"),
        ({"date": "2026-08-10T10:00:00"}, "no UTC offset"),
        ({"quantity": "lots"}, "malformed"),
        ({"unitPrice": -1}, "must not be negative"),
    ],
)
def test_invalid_items_are_not_compared(overrides, reason):
    [item] = run([proposed(**overrides)], [existing("e1")])["items"]

    assert item["status"] == "invalid"
    assert reason in item["reasons"][0]
    assert item["matches"] == []


def test_warnings_do_not_change_the_status():
    items = [
        proposed(date="2026-08-09T22:00:00.000Z"),  # local midnight Aug 10
        proposed(date="2026-08-12"),  # date only
        proposed(date="2026-09-05T10:00:00.000Z"),  # future
        proposed(accountId="acc-zz"),
        proposed(accountId=None),
    ]

    result = run(items)
    warnings = [i["warnings"] for i in result["items"]]

    assert [i["status"] for i in result["items"]] == ["new", "new", "new", "new", "new"]
    assert "previous UTC day" in warnings[0][0]
    assert warnings[1] == ["date-only value is read as UTC midnight"]
    assert warnings[2] == ["date is in the future"]
    assert warnings[3] == ["unknown accountId 'acc-zz'"]
    assert "no accountId" in warnings[4][0]


def test_summary_and_determinism():
    current = [existing("e2"), existing("e1", quantity=5)]
    items = [proposed(), proposed(symbol="NEW"), proposed(symbol=None)]

    first = run(items, current)
    second = run(items, current[::-1])

    assert first["summary"] == {
        "total": 3,
        "new": 1,
        "duplicate": 1,
        "conflict": 0,
        "invalid": 1,
    }
    assert first["items"][0]["matches"] == ["e2"]
    assert first["method_version"] == METHOD_VERSION
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
