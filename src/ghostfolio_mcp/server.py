#!/usr/bin/env python3
"""
Ghostfolio MCP Server

Provides a Model Context Protocol (MCP) server exposing tools that interact with the Ghostfolio API.
"""

import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp import settings
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.server.transforms.search import RegexSearchTransform

from ghostfolio_mcp.ghostfolio_client import get_ghostfolio_config_from_env
from ghostfolio_mcp.ghostfolio_client import get_transport_config_from_env
from ghostfolio_mcp.ghostfolio_tools import register_tools
from ghostfolio_mcp.sentry_init import init_sentry

# Load environment variables
load_dotenv()

# Configure FastMCP defaults
settings.show_server_banner = False
settings.check_for_updates = "off"

# Initialize optional Sentry monitoring
init_sentry()

# Configure logging
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Get package version
try:
    __version__ = version("ghostfolio-mcp")
except PackageNotFoundError:
    __version__ = "0.0.1"

try:
    GHOSTFOLIO_CONFIG = get_ghostfolio_config_from_env()
    TRANSPORT_CONFIG = get_transport_config_from_env()
except Exception as e:
    logger.error(f"Invalid configuration: {e}")
    raise

# Create auth provider. Precedence: OIDC (remote OAuth via an upstream IdP such as
# PocketID) > static bearer token > none. OIDC exposes a DCR-compliant OAuth interface
# required by Claude's remote connectors (mobile/desktop); the static bearer path is
# kept for machine-to-machine clients (e.g. Claude Code CLI).
auth_provider = None
if TRANSPORT_CONFIG.oidc_enabled:
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    auth_provider = OIDCProxy(
        config_url=TRANSPORT_CONFIG.oidc_config_url,
        client_id=TRANSPORT_CONFIG.oidc_client_id,
        client_secret=TRANSPORT_CONFIG.oidc_client_secret,
        base_url=TRANSPORT_CONFIG.oidc_base_url,
        redirect_path=TRANSPORT_CONFIG.oidc_redirect_path,
        required_scopes=TRANSPORT_CONFIG.oidc_required_scopes,
        allowed_client_redirect_uris=TRANSPORT_CONFIG.oidc_allowed_redirect_uris,
        verify_id_token=TRANSPORT_CONFIG.oidc_verify_id_token,
        forward_resource=TRANSPORT_CONFIG.oidc_forward_resource,
    )
    logger.info(
        "OIDC auth enabled via OIDCProxy (upstream: %s)",
        TRANSPORT_CONFIG.oidc_config_url,
    )
elif getattr(TRANSPORT_CONFIG, "http_bearer_token", None):
    bearer_token = TRANSPORT_CONFIG.http_bearer_token
    if bearer_token:  # Type narrowing: ensures bearer_token is str, not None
        auth_provider = StaticTokenVerifier(
            tokens={
                bearer_token: {
                    "client_id": "authenticated-client",
                    "scopes": ["read", "write"],
                }
            }
        )

# Initialize FastMCP server
mcp = FastMCP(
    name="Ghostfolio MCP Server",
    version=__version__,
    instructions=(
        "This MCP server exposes tools for interacting with the Ghostfolio API, supporting both read and write operations if not in read-only mode."
    ),
    auth=auth_provider,
)

# Register all tools
register_tools(mcp, GHOSTFOLIO_CONFIG)


def configure_component_visibility() -> None:
    """Apply server-level visibility transforms for read-only and disabled tags."""

    disabled_tags = getattr(GHOSTFOLIO_CONFIG, "disabled_tags", set())
    read_only_mode = getattr(GHOSTFOLIO_CONFIG, "read_only_mode", False)

    if read_only_mode:
        logger.info("Read-only mode is enabled - restricting to read-only components")
        mcp.enable(tags={"read-only"}, only=True)

    if disabled_tags:
        logger.info(
            "Disabled tags configured: %s - disabling matching components",
            disabled_tags,
        )
        mcp.disable(tags=disabled_tags)


def configure_tool_search() -> None:
    """Apply the optional FastMCP tool-search transform."""

    if not getattr(GHOSTFOLIO_CONFIG, "tool_search_enabled", False):
        return

    strategy = getattr(GHOSTFOLIO_CONFIG, "tool_search_strategy", "bm25")
    max_results = getattr(GHOSTFOLIO_CONFIG, "tool_search_max_results", 5)

    if strategy == "regex":
        mcp.add_transform(RegexSearchTransform(max_results=max_results))
    else:
        mcp.add_transform(BM25SearchTransform(max_results=max_results))

    logger.info(
        "Tool search is enabled - strategy=%s, max_results=%s",
        strategy,
        max_results,
    )


configure_component_visibility()
configure_tool_search()

# Optional rate limiting
if getattr(GHOSTFOLIO_CONFIG, "rate_limit_enabled", False):
    logger.info("Rate limiting is enabled - applying middleware")
    mcp.add_middleware(
        SlidingWindowRateLimitingMiddleware(
            max_requests=GHOSTFOLIO_CONFIG.rate_limit_max_requests,
            window_minutes=GHOSTFOLIO_CONFIG.rate_limit_window_minutes,
        )
    )


def _http_security_kwargs() -> dict:
    """Host/Origin guard config for HTTP/SSE transports.

    FastMCP's request guard only allows localhost by default and returns 421 for a
    proxied Host header (and 403 for a mismatched Origin). Behind a TLS reverse proxy
    the real controls are TLS + OAuth, so the guard defaults OFF for remote hosting.
    Set MCP_HOST_ORIGIN_PROTECTION=true to harden: the public host/origin (derived
    from OIDC_BASE_URL) and Claude's connector origins are then allowed automatically,
    alongside any MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS.
    """
    hop = TRANSPORT_CONFIG.host_origin_protection
    if not hop:
        logger.info(
            "Host/Origin protection disabled (expected behind a TLS reverse proxy)"
        )
        return {"host_origin_protection": False}

    allowed_hosts = list(TRANSPORT_CONFIG.allowed_hosts or [])
    allowed_origins = list(TRANSPORT_CONFIG.allowed_origins or [])
    if TRANSPORT_CONFIG.oidc_base_url:
        parsed = urlsplit(TRANSPORT_CONFIG.oidc_base_url)
        if parsed.hostname and parsed.hostname not in allowed_hosts:
            allowed_hosts.append(parsed.hostname)
        public_origin = f"{parsed.scheme}://{parsed.netloc}"
        if public_origin not in allowed_origins:
            allowed_origins.append(public_origin)
    for claude_origin in ("https://claude.ai", "https://claude.com"):
        if claude_origin not in allowed_origins:
            allowed_origins.append(claude_origin)

    logger.info(
        "Host/Origin protection enabled (hosts=%s, origins=%s)",
        allowed_hosts,
        allowed_origins,
    )
    return {
        "host_origin_protection": True,
        "allowed_hosts": allowed_hosts,
        "allowed_origins": allowed_origins,
    }


def main():
    # Basic validation
    if not all([GHOSTFOLIO_CONFIG.ghostfolio_url, GHOSTFOLIO_CONFIG.token]):
        logger.error(
            "Missing required Ghostfolio configuration (GHOSTFOLIO_URL or GHOSTFOLIO_TOKEN). Check your .env file."
        )
        raise SystemExit(1)

    if (
        TRANSPORT_CONFIG.transport_type in {"sse", "http"}
        and not TRANSPORT_CONFIG.http_bearer_token
        and not TRANSPORT_CONFIG.oidc_enabled
    ):
        logger.warning(
            "WARNING: MCP_HTTP_BEARER_TOKEN is not set. The MCP server will run WITHOUT authentication. "
            "Ensure the server is not exposed to untrusted networks (e.g. bind to 127.0.0.1 instead of 0.0.0.0)."
        )

    logger.info(
        f"Starting Ghostfolio MCP Server at {GHOSTFOLIO_CONFIG.ghostfolio_url} ..."
    )

    # Choose transport based on configuration
    if TRANSPORT_CONFIG.transport_type == "sse":
        logger.info(
            f"Using HTTP SSE transport on {TRANSPORT_CONFIG.http_host}:{TRANSPORT_CONFIG.http_port}"
        )
        if TRANSPORT_CONFIG.http_bearer_token:
            logger.info("Bearer token authentication enabled for SSE transport")

        # Run with HTTP SSE transport
        mcp.run(
            transport="sse",
            host=TRANSPORT_CONFIG.http_host,
            port=TRANSPORT_CONFIG.http_port,
            **_http_security_kwargs(),
        )
    elif TRANSPORT_CONFIG.transport_type == "http":
        logger.info(
            f"Using HTTP Streamable transport on {TRANSPORT_CONFIG.http_host}:{TRANSPORT_CONFIG.http_port}"
        )
        if TRANSPORT_CONFIG.http_bearer_token:
            logger.info("Bearer token authentication enabled for Streamable transport")

        # Run with HTTP Streamable transport
        mcp.run(
            transport="http",
            host=TRANSPORT_CONFIG.http_host,
            port=TRANSPORT_CONFIG.http_port,
            **_http_security_kwargs(),
        )
    else:
        # Default to STDIO transport
        logger.info("Using STDIO transport")
        mcp.run()


if __name__ == "__main__":
    main()
