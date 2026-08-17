import pytest
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from ghostfolio_mcp.auth import build_auth_provider
from ghostfolio_mcp.auth import http_security_kwargs
from ghostfolio_mcp.models import TransportConfig

OIDC_SETTINGS = {
    "oidc_config_url": "https://id.example.com/.well-known/openid-configuration",
    "oidc_client_id": "client-id",
    "oidc_client_secret": "client-secret",
    "oidc_base_url": "https://mcp.example.com",
}


# Stand-in for OIDCProxy, which performs OIDC discovery when constructed.
class FakeOIDCProxy:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


# Patches the OIDCProxy that build_auth_provider imports lazily.
@pytest.fixture
def fake_oidc_proxy(monkeypatch):
    monkeypatch.setattr(
        "fastmcp.server.auth.oidc_proxy.OIDCProxy", FakeOIDCProxy, raising=True
    )
    return FakeOIDCProxy


def test_build_auth_provider_returns_none_without_auth():
    assert build_auth_provider(TransportConfig()) is None


# OIDC is optional, so an unconfigured server must not be warned at.
def test_build_auth_provider_is_quiet_when_oidc_is_unconfigured(caplog):
    with caplog.at_level("WARNING"):
        provider = build_auth_provider(TransportConfig())

    assert provider is None
    assert caplog.records == []


def test_partial_oidc_config_warns_once_and_falls_back(caplog):
    config = TransportConfig(
        http_bearer_token="secret-token",
        oidc_config_url="https://id.example.com",
        oidc_client_id="client-id",
    )

    with caplog.at_level("WARNING"):
        provider = build_auth_provider(config)

    assert isinstance(provider, StaticTokenVerifier)
    assert len(caplog.records) == 1
    assert "OIDC_CLIENT_SECRET, OIDC_BASE_URL" in caplog.records[0].message


def test_build_auth_provider_uses_bearer_token():
    provider = build_auth_provider(TransportConfig(http_bearer_token="secret-token"))

    assert isinstance(provider, StaticTokenVerifier)


@pytest.mark.usefixtures("fake_oidc_proxy")
def test_build_auth_provider_prefers_oidc_over_bearer_token():
    config = TransportConfig(http_bearer_token="secret-token", **OIDC_SETTINGS)

    provider = build_auth_provider(config)

    assert isinstance(provider, FakeOIDCProxy)


@pytest.mark.usefixtures("fake_oidc_proxy")
def test_build_auth_provider_passes_oidc_settings_through():
    config = TransportConfig(
        **OIDC_SETTINGS,
        oidc_redirect_path="/oauth/callback",
        oidc_required_scopes=["openid", "profile"],
        oidc_allowed_redirect_uris=["https://example.com/*"],
        oidc_verify_id_token=True,
        oidc_forward_resource=True,
    )

    provider = build_auth_provider(config)

    assert isinstance(provider, FakeOIDCProxy)
    assert provider.kwargs == {
        "config_url": OIDC_SETTINGS["oidc_config_url"],
        "client_id": "client-id",
        "client_secret": "client-secret",
        "base_url": "https://mcp.example.com",
        "redirect_path": "/oauth/callback",
        "required_scopes": ["openid", "profile"],
        "allowed_client_redirect_uris": ["https://example.com/*"],
        "verify_id_token": True,
        "forward_resource": True,
    }


@pytest.mark.usefixtures("fake_oidc_proxy")
# The defaults that differ from FastMCP's own must not drift silently.
def test_oidc_defaults_are_conservative():
    provider = build_auth_provider(TransportConfig(**OIDC_SETTINGS))

    assert isinstance(provider, FakeOIDCProxy)
    # FastMCP defaults forward_resource to True, which identity providers without
    # RFC 8707 support reject with invalid_request.
    assert provider.kwargs["forward_resource"] is False
    assert provider.kwargs["verify_id_token"] is False
    assert provider.kwargs["redirect_path"] == "/auth/callback"


# An unset guard must leave FastMCP's own default in place.
def test_http_security_kwargs_untouched_when_unset():
    assert http_security_kwargs(TransportConfig()) == {}


def test_http_security_kwargs_explicit_opt_out():
    config = TransportConfig(host_origin_protection=False)

    assert http_security_kwargs(config) == {"host_origin_protection": False}


def test_http_security_kwargs_allows_public_host_from_oidc_base_url():
    config = TransportConfig(host_origin_protection=True, **OIDC_SETTINGS)

    kwargs = http_security_kwargs(config)

    assert kwargs["host_origin_protection"] is True
    assert kwargs["allowed_hosts"] == ["mcp.example.com"]
    assert kwargs["allowed_origins"] == ["https://mcp.example.com"]


def test_http_security_kwargs_keeps_non_default_port_in_origin():
    config = TransportConfig(
        host_origin_protection=True,
        **{**OIDC_SETTINGS, "oidc_base_url": "https://mcp.example.com:8443"},
    )

    kwargs = http_security_kwargs(config)

    assert kwargs["allowed_hosts"] == ["mcp.example.com"]
    assert kwargs["allowed_origins"] == ["https://mcp.example.com:8443"]


def test_http_security_kwargs_merges_configured_entries():
    config = TransportConfig(
        host_origin_protection=True,
        allowed_hosts=["extra.example.com"],
        allowed_origins=["https://app.example.com"],
        **OIDC_SETTINGS,
    )

    kwargs = http_security_kwargs(config)

    assert kwargs["allowed_hosts"] == ["extra.example.com", "mcp.example.com"]
    assert kwargs["allowed_origins"] == [
        "https://app.example.com",
        "https://mcp.example.com",
    ]


def test_http_security_kwargs_does_not_duplicate_configured_entries():
    config = TransportConfig(
        host_origin_protection=True,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://mcp.example.com"],
        **OIDC_SETTINGS,
    )

    kwargs = http_security_kwargs(config)

    assert kwargs["allowed_hosts"] == ["mcp.example.com"]
    assert kwargs["allowed_origins"] == ["https://mcp.example.com"]


# Empty lists would read as a deliberately empty allow-list to FastMCP.
def test_http_security_kwargs_without_oidc_passes_none_lists():
    kwargs = http_security_kwargs(TransportConfig(host_origin_protection=True))

    assert kwargs == {
        "host_origin_protection": True,
        "allowed_hosts": None,
        "allowed_origins": None,
    }
