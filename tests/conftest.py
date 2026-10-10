"""Shared fixtures."""

from datetime import date

import httpx2
import pytest
from fastmcp import FastMCP

from ghostfolio_mcp import ghostfolio_client as client_module
from ghostfolio_mcp.ghostfolio_client import GhostfolioClient
from ghostfolio_mcp.models import GhostfolioConfig
from ghostfolio_mcp.tools import analytics as analytics_module
from ghostfolio_mcp.tools import register_tools

BASE_URL = "https://ghostfolio.test:3333"
AUTH_PATH = "/api/v1/auth/anonymous/"


@pytest.fixture
def fake_ghostfolio(monkeypatch):
    """Build a FastMCP server backed by canned Ghostfolio responses.

    Call it with ``responses`` (URL path -> JSON body, or a callable taking the
    request and returning one), optional ``today`` and GhostfolioConfig
    overrides; it returns ``(call_tool, requests)``, where ``requests`` records
    every non-auth request and unknown paths answer 404.
    """
    created = []

    def build(responses, today=date(2026, 5, 10), **config_overrides):
        monkeypatch.setattr(analytics_module, "_today", lambda _tz: today)
        config = GhostfolioConfig(
            ghostfolio_url=BASE_URL, token="api-token", **config_overrides
        )
        mcp = FastMCP(name="test")
        register_tools(mcp, config)
        requests: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url.path == AUTH_PATH:
                return httpx2.Response(200, json={"authToken": "jwt"})
            requests.append(request)
            body = responses.get(request.url.path)
            if callable(body):
                body = body(request)
            if body is None:
                return httpx2.Response(404, json={})
            return httpx2.Response(200, json=body)

        GhostfolioClient._instance = None
        client_module._ghostfolio_client_singleton = None
        client = client_module.get_ghostfolio_client(config)
        client.client = httpx2.AsyncClient(
            base_url=client.base_url, transport=httpx2.MockTransport(handler)
        )
        created.append(client)

        async def call_tool(name, arguments):
            tool = await mcp.get_tool(name)
            assert tool is not None, f"tool {name!r} is not registered"
            result = (await tool.run(arguments)).structured_content
            assert result is not None
            return result

        return call_tool, requests

    yield build
    GhostfolioClient._instance = None
    client_module._ghostfolio_client_singleton = None
