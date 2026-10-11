from typing import Literal

from pydantic import BaseModel
from pydantic import Field


class InterestRuleConfig(BaseModel):
    """Per-account interest attribution rule, used by get_monthly_returns."""

    attribution: Literal["payment_date", "previous_month"] = Field(
        "payment_date",
        description="Month interest is attributed to under 'accrual' attribution",
    )
    expected: Literal["none", "daily", "monthly", "quarterly"] = Field(
        "none",
        description="Known payment cadence; a day or month without interest is flagged",
    )


class TaxProfile(BaseModel):
    """Per-account tax settings, used by get_realized_gains."""

    excluded: bool = Field(
        False,
        description="Leave the account out of capital gains (e.g. a pension plan)",
    )


class Modelo720Profile(BaseModel):
    """Per-account Modelo 720 settings, used by get_modelo720_inputs."""

    foreign: bool = Field(
        False, description="The account is held abroad and counts for Modelo 720"
    )


class AccountProfile(BaseModel):
    """Everything the analytics tools need to know about one account."""

    interest: InterestRuleConfig = Field(default_factory=InterestRuleConfig)
    tax: TaxProfile = Field(default_factory=TaxProfile)
    modelo720: Modelo720Profile = Field(default_factory=Modelo720Profile)


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
    account_profiles: dict[str, AccountProfile] = Field(
        default_factory=dict,
        description="Per-account analytics settings keyed by Ghostfolio account ID",
    )
    homogeneous_groups: list[list[str]] = Field(
        default_factory=list,
        description=(
            "Groups of DATASOURCE:SYMBOL counted as one asset by the two-month "
            "rule of get_realized_gains (equivalent securities)"
        ),
    )
    default_benchmark: str | None = Field(
        None,
        description="Benchmark as DATASOURCE:SYMBOL when a tool is not given one",
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
    # OIDC / OAuth. When config_url, client_id, client_secret and base_url are all
    # set, the server exposes an OAuth interface with Dynamic Client Registration
    # (FastMCP's OIDCProxy) brokered to an upstream identity provider, so MCP clients
    # that can only authenticate over OAuth are able to connect.
    oidc_config_url: str | None = Field(
        None,
        description="Upstream OIDC discovery URL (.../.well-known/openid-configuration)",
    )
    oidc_client_id: str | None = Field(
        None, description="Client ID registered with the upstream identity provider"
    )
    oidc_client_secret: str | None = Field(
        None, description="Client secret registered with the upstream identity provider"
    )
    oidc_base_url: str | None = Field(
        None,
        description="Public base URL where this server's OAuth endpoints are reachable",
    )
    oidc_redirect_path: str = Field(
        "/auth/callback",
        description="Callback path registered on the upstream identity provider",
    )
    oidc_required_scopes: list[str] | None = Field(
        None, description="Scopes required on presented tokens (None = do not enforce)"
    )
    oidc_allowed_redirect_uris: list[str] | None = Field(
        None,
        description=(
            "Allowed MCP client redirect URI patterns (wildcards accepted). "
            "None = OIDCProxy default (registered URIs plus loopback variance)"
        ),
    )
    oidc_verify_id_token: bool = Field(
        False,
        description=(
            "Verify the id_token instead of the access_token, for identity providers "
            "that issue opaque (non-JWT) access tokens"
        ),
    )
    oidc_forward_resource: bool = Field(
        False,
        description=(
            "Forward the RFC 8707 'resource' indicator to the upstream identity "
            "provider. Off by default because providers that do not implement "
            "resource indicators reject the authorization request with "
            "invalid_request. Token audience binding is unaffected."
        ),
    )
    # Host/Origin request guard (FastMCP's DNS-rebinding protection). None leaves
    # FastMCP's own default in place; setting it explicitly overrides that default.
    host_origin_protection: bool | None = Field(
        None,
        description=(
            "Validate Host and Origin headers on HTTP/SSE transports. "
            "None = leave FastMCP's default in place"
        ),
    )
    allowed_hosts: list[str] | None = Field(
        None,
        description="Extra Host header values accepted when host_origin_protection is on",
    )
    allowed_origins: list[str] | None = Field(
        None,
        description="Extra browser Origins accepted when host_origin_protection is on",
    )

    def missing_oidc_settings(self) -> list[str]:
        """Names of OIDC settings that are needed but absent.

        Empty when OIDC is fully configured and when it is not configured at all;
        non-empty only for a half-configured setup, which is worth a warning
        because the server then falls back to whatever else is configured.
        """
        provided = {
            "OIDC_CONFIG_URL": self.oidc_config_url,
            "OIDC_CLIENT_ID": self.oidc_client_id,
            "OIDC_CLIENT_SECRET": self.oidc_client_secret,
            "OIDC_BASE_URL": self.oidc_base_url,
        }
        missing = [name for name, value in provided.items() if not value]
        return [] if len(missing) == len(provided) else missing

    def oidc_credentials(self) -> tuple[str, str, str, str] | None:
        """Return the four required OIDC settings, or None when OIDC is off."""
        if not (
            self.oidc_config_url
            and self.oidc_client_id
            and self.oidc_client_secret
            and self.oidc_base_url
        ):
            return None
        return (
            self.oidc_config_url,
            self.oidc_client_id,
            self.oidc_client_secret,
            self.oidc_base_url,
        )

    @property
    def oidc_enabled(self) -> bool:
        """OIDC is active only when every upstream credential is present."""
        return self.oidc_credentials() is not None
