"""Tests for the preview_import tool: it must never write."""

import pytest
from fastmcp.exceptions import ToolError

RESPONSES = {
    "/api/v1/activities/": {
        "activities": [
            {
                "id": "e1",
                "type": "BUY",
                "date": "2026-08-10T10:00:00.000Z",
                "accountId": "acc-a",
                "account": {"id": "acc-a", "name": "A"},
                "SymbolProfile": {"symbol": "ETF", "dataSource": "YAHOO"},
                "quantity": 1,
                "unitPrice": 1.5,
                "fee": 0,
                "feeInBaseCurrency": 0,
                "valueInBaseCurrency": 1.5,
                "currency": "EUR",
            }
        ],
        "count": 1,
    },
    "/api/v1/account/": {"accounts": [{"id": "acc-a", "name": "A"}]},
}

PAYLOAD = {
    "activities": [
        {
            "accountId": "acc-a",
            "currency": "EUR",
            "dataSource": "YAHOO",
            "date": "2026-08-10T15:00:00.000Z",
            "fee": 0,
            "quantity": 1,
            "symbol": "ETF",
            "type": "BUY",
            "unitPrice": 1.5,
        }
    ]
}


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only_mode", [False, True])
async def test_preview_only_reads_even_in_read_only_mode(
    fake_ghostfolio, read_only_mode
):
    call_tool, requests = fake_ghostfolio(RESPONSES, read_only_mode=read_only_mode)

    result = await call_tool("preview_import", {"data": PAYLOAD})

    assert {r.method for r in requests} == {"GET"}
    assert sorted({r.url.path for r in requests}) == sorted(RESPONSES)
    assert result["items"][0]["status"] == "duplicate"
    assert result["items"][0]["matches"] == ["e1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [{}, {"activities": "x"}, {"activities": [1]}])
async def test_malformed_payload_is_refused_before_any_request(fake_ghostfolio, data):
    call_tool, requests = fake_ghostfolio(RESPONSES)

    with pytest.raises(ToolError, match="activities"):
        await call_tool("preview_import", {"data": data})
    assert requests == []
