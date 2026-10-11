"""Tests for the audit_portfolio_data tool: what it reads from Ghostfolio."""

from datetime import date

import pytest

from ghostfolio_mcp.models import AccountProfile
from ghostfolio_mcp.models import InterestRuleConfig

ACTIVITIES = [
    {
        "id": "b1",
        "type": "BUY",
        "date": "2026-03-29T22:00:00.000Z",
        "accountId": "acc-a",
        "account": {"id": "acc-a", "name": "Broker A"},
        "SymbolProfile": {"symbol": "ETF", "dataSource": "YAHOO"},
        "quantity": 1,
        "unitPrice": 1.1,
        "feeInBaseCurrency": 0,
        "valueInBaseCurrency": 1.1,
        "currency": "EUR",
    },
    {
        "id": "i1",
        "type": "INTEREST",
        "date": "2026-04-01T09:00:00.000Z",
        "accountId": "acc-a",
        "account": {"id": "acc-a", "name": "Broker A"},
        "SymbolProfile": {"symbol": "GF_INTEREST-CASH", "dataSource": "MANUAL"},
        "quantity": 1,
        "unitPrice": 0.01,
        "feeInBaseCurrency": 0,
        "valueInBaseCurrency": 0.01,
        "currency": "EUR",
    },
]

# A daily cash-interest series from Apr 1 to Apr 9 with Apr 5 missing.
ACTIVITIES += [
    {
        **ACTIVITIES[1],
        "id": f"i{day}",
        "date": f"2026-04-{day:02d}T09:00:00.000Z",
    }
    for day in (2, 3, 4, 6, 7, 8, 9)
]

RESPONSES = {
    "/api/v1/activities/": {"activities": ACTIVITIES, "count": len(ACTIVITIES)},
    "/api/v1/account/": {
        "accounts": [
            {"id": "acc-a", "name": "Broker A", "balance": 150.0, "tags": []},
            {
                "id": "acc-b",
                "name": "Bank B",
                "balance": 160.0,
                "tags": [{"name": "EXCLUDE_FROM_ANALYSIS"}],
            },
        ]
    },
    "/api/v1/account/acc-a/balances/": {
        "balances": [{"date": "2026-04-05T00:00:00.000Z", "value": 150.0}]
    },
    "/api/v1/account/acc-b/balances/": {"balances": []},
    "/api/v2/portfolio/performance/": {"chart": []},
    "/api/v1/asset/YAHOO/ETF/": {
        "assetProfile": {"isin": None},
        "marketData": [
            {
                "date": "2026-03-29T00:00:00.000Z",
                "marketPrice": 1.0,
                "isCarriedForward": True,
            },
            {
                "date": "2026-03-30T00:00:00.000Z",
                "marketPrice": 1.1,
                "isCarriedForward": False,
            },
            {
                "date": "2026-04-09T00:00:00.000Z",
                "marketPrice": 1.1,
                "isCarriedForward": False,
            },
        ],
    },
    "/api/v1/user/": {"settings": {"baseCurrency": "EUR"}},
    "/api/v1/import/dividends/YAHOO/ETF/": {
        "activities": [
            {
                "date": "2026-03-31T00:00:00.000Z",
                "type": "DIVIDEND",
                "quantity": 1,
                "unitPrice": 0.02,
                "accountId": "acc-a",
            }
        ]
    },
}


@pytest.mark.asyncio
async def test_audit_reads_only_and_reports_findings(fake_ghostfolio):
    call_tool, requests = fake_ghostfolio(
        RESPONSES,
        today=date(2026, 4, 11),
        account_profiles={
            "acc-a": AccountProfile(interest=InterestRuleConfig(expected="daily"))
        },
    )

    result = await call_tool("audit_portfolio_data", {})

    assert {r.method for r in requests} == {"GET"}
    assert sorted({r.url.path for r in requests}) == sorted(RESPONSES)
    assert result["as_of"] == "2026-04-10"
    found = {(f["check"], f["severity"]) for f in result["findings"]}
    assert ("trade_date_mismatch", "warn") in found
    [missing] = [f for f in result["findings"] if f["check"] == "missing_interest_day"]
    # Apr 10 is as_of: within the one-day grace for daily payments.
    assert missing["dates"] == ["2026-04-05"]
    assert ("missing_isin", "info") in found
    # acc-b holds cash without interest but is excluded from analysis.
    assert [
        f for f in result["findings"] if f["check"] == "cash_without_interest"
    ] == []
    [distribution] = [
        f for f in result["findings"] if f["check"] == "missing_distribution"
    ]
    assert distribution["security"] == "YAHOO:ETF"
    assert distribution["evidence"]["expected_gross"] == 0.02


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "flagged"),
    [
        # Unset: "none" is only the default, the account is still checked.
        ({"modelo720": {"foreign": True}}, True),
        # Explicit: the account is known to pay no interest.
        ({"interest": {"expected": "none"}}, False),
    ],
)
async def test_only_an_explicit_no_interest_profile_silences_the_check(
    fake_ghostfolio, profile, flagged
):
    # acc-b as a regular (not excluded) account holding cash without interest.
    responses = {
        **RESPONSES,
        "/api/v1/account/": {
            "accounts": [
                {"id": "acc-a", "name": "Broker A", "balance": 150.0, "tags": []},
                {"id": "acc-b", "name": "Bank B", "balance": 160.0, "tags": []},
            ]
        },
    }
    call_tool, _requests = fake_ghostfolio(
        responses,
        today=date(2026, 4, 11),
        account_profiles={"acc-b": AccountProfile.model_validate(profile)},
    )

    result = await call_tool("audit_portfolio_data", {})
    cash = [
        f["account_id"]
        for f in result["findings"]
        if f["check"] == "cash_without_interest"
    ]

    assert cash == (["acc-b"] if flagged else [])
