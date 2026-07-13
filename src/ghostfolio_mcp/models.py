from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class GhostfolioConfig(BaseModel):
    ghostfolio_url: str = Field(
        ..., description="Ghostfolio base URL, e.g. https://domain.tld:3333"
    )
    token: str = Field(..., description="Ghostfolio API token")
    verify_ssl: bool = Field(True, description="Verify SSL (true/false)")
    timeout: int = Field(30, description="Timeout in seconds")
    read_only_mode: bool = Field(False, description="Read-only mode (true/false)")
    disabled_tags: set[str] = Field(
        default_factory=set, description="Set of tags to disable tools for"
    )
    rate_limit_enabled: bool = Field(
        False, description="Enable rate limiting (true/false)"
    )
    rate_limit_max_requests: int = Field(60, description="Maximum requests per minute")
    rate_limit_window_minutes: int = Field(
        1, description="Rate limit window in minutes"
    )
    tool_search_enabled: bool = Field(
        False, description="Enable FastMCP tool search transform"
    )
    tool_search_strategy: Literal["bm25", "regex"] = Field(
        "bm25", description="Tool search strategy: 'bm25' (natural language) or 'regex'"
    )
    tool_search_max_results: int = Field(
        5,
        ge=1,
        description="Maximum number of tools returned by search_tools",
    )


class TransportConfig(BaseModel):
    """Configuration for MCP transport layer"""

    transport_type: str = Field(
        "stdio",
        description="Transport type: 'stdio', 'sse' (Server-Sent Events), or 'http' (HTTP Streamable)",
    )
    # HTTP transport settings (for both SSE and HTTP Streamable)
    http_host: str = Field(
        "127.0.0.1",
        description="Host to bind for HTTP transports (SSE/HTTP Streamable)",
    )
    http_port: int = Field(
        8000, description="Port to bind for HTTP transports (SSE/HTTP Streamable)"
    )
    http_bearer_token: str | None = Field(
        None, description="Bearer token for HTTP authentication"
    )
    # OIDC / OAuth (remote MCP via an upstream OIDC IdP, e.g. PocketID).
    # When all of config_url/client_id/client_secret/base_url are set, the server
    # exposes a DCR-compliant OAuth interface (FastMCP OIDCProxy) brokered to the
    # upstream IdP, so Claude's remote connectors (mobile/desktop) can authenticate.
    oidc_config_url: str | None = Field(
        None,
        description="Upstream OIDC discovery URL (…/.well-known/openid-configuration)",
    )
    oidc_client_id: str | None = Field(
        None, description="Client ID registered with the upstream IdP"
    )
    oidc_client_secret: str | None = Field(
        None, description="Client secret registered with the upstream IdP"
    )
    oidc_base_url: str | None = Field(
        None,
        description="Public base URL where this server's OAuth endpoints are reachable",
    )
    oidc_redirect_path: str = Field(
        "/auth/callback",
        description="Callback path registered on the upstream IdP client",
    )
    oidc_required_scopes: list[str] | None = Field(
        None, description="Scopes required on presented tokens (None = don't enforce)"
    )
    oidc_allowed_redirect_uris: list[str] | None = Field(
        None,
        description=(
            "Allowed MCP-client redirect URI patterns (wildcards ok). "
            "None = OIDCProxy default (registered URIs + loopback variance)"
        ),
    )
    oidc_verify_id_token: bool = Field(
        False,
        description="Verify the id_token instead of the access_token (for IdPs that issue opaque access tokens)",
    )
    oidc_forward_resource: bool = Field(
        False,
        description=(
            "Forward the RFC 8707 'resource' indicator upstream. Default off: many "
            "OIDC IdPs (e.g. PocketID) reject it with invalid_request. Enable only for "
            "IdPs that support resource indicators. Token audience binding is unaffected."
        ),
    )
    # Host/Origin request guard (FastMCP DNS-rebinding protection). It allows only
    # localhost by default and 421s a proxied Host header, so it must be relaxed when
    # the server runs behind a reverse proxy. Default (None) = off for HTTP/SSE.
    host_origin_protection: bool | None = Field(
        None,
        description="Validate Host/Origin headers. Default: off for HTTP/SSE (intended behind a TLS reverse proxy); set true to harden.",
    )
    allowed_hosts: list[str] | None = Field(
        None,
        description="Extra Host header values allowed when host_origin_protection is on",
    )
    allowed_origins: list[str] | None = Field(
        None,
        description="Extra browser Origins allowed when host_origin_protection is on",
    )

    @property
    def oidc_enabled(self) -> bool:
        """OIDC auth is active only when all upstream credentials are present."""
        return bool(
            self.oidc_config_url
            and self.oidc_client_id
            and self.oidc_client_secret
            and self.oidc_base_url
        )
