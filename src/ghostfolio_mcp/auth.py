"""Authentication and HTTP request-guard configuration for the MCP server.

Kept separate from server.py so the policy decisions here (which auth provider
to build, whether to harden the Host/Origin guard) are pure functions of the
transport config and can be tested without importing the server module.
"""

import logging
from typing import Any
from urllib.parse import urlsplit

from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

from ghostfolio_mcp.models import TransportConfig

logger = logging.getLogger(__name__)


def build_auth_provider(config: TransportConfig) -> Any | None:
    """Build the auth provider for the configured transport.

    Precedence: OIDC > static bearer token > no authentication.

    OIDC exposes a Dynamic Client Registration compliant OAuth interface
    brokered to an upstream identity provider, which is what MCP clients that
    only speak OAuth (for example remote connectors) require. The static bearer
    token is kept for machine-to-machine clients.

    OIDC is entirely optional: leaving its settings unset simply falls through
    to the existing behaviour.
    """
    missing = config.missing_oidc_settings()
    if missing:
        logger.warning(
            "OIDC is only partially configured (missing %s) and has been ignored. "
            "Set all of OIDC_CONFIG_URL, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET and "
            "OIDC_BASE_URL to enable it.",
            ", ".join(missing),
        )

    credentials = config.oidc_credentials()
    if credentials is not None:
        config_url, client_id, client_secret, base_url = credentials

        # Imported lazily: constructing the provider performs OIDC discovery, and
        # STDIO users should not pay for the import at all.
        from fastmcp.server.auth.oidc_proxy import OIDCProxy

        logger.info("OIDC authentication enabled (upstream: %s)", config_url)
        return OIDCProxy(
            config_url=config_url,
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            redirect_path=config.oidc_redirect_path,
            required_scopes=config.oidc_required_scopes,
            allowed_client_redirect_uris=config.oidc_allowed_redirect_uris,
            verify_id_token=config.oidc_verify_id_token,
            forward_resource=config.oidc_forward_resource,
        )

    if config.http_bearer_token:
        return StaticTokenVerifier(
            tokens={
                config.http_bearer_token: {
                    "client_id": "authenticated-client",
                    "scopes": ["read", "write"],
                }
            }
        )

    return None


def http_security_kwargs(config: TransportConfig) -> dict[str, Any]:
    """Host/Origin guard arguments for mcp.run() on HTTP/SSE transports.

    FastMCP validates the Host and Origin headers to defend against DNS
    rebinding. Its own default is left untouched unless
    MCP_HOST_ORIGIN_PROTECTION is set explicitly, so this returns an empty
    mapping when the setting is unset.

    When hardening is enabled, only loopback hosts are accepted out of the box,
    which rejects a reverse-proxied Host header with 421. The public host and
    origin implied by OIDC_BASE_URL are therefore allowed automatically,
    alongside anything listed in MCP_ALLOWED_HOSTS / MCP_ALLOWED_ORIGINS.
    """
    if config.host_origin_protection is None:
        return {}

    if not config.host_origin_protection:
        logger.info(
            "Host/Origin protection explicitly disabled - only do this behind a "
            "trusted reverse proxy that terminates TLS"
        )
        return {"host_origin_protection": False}

    allowed_hosts = list(config.allowed_hosts or [])
    allowed_origins = list(config.allowed_origins or [])

    if config.oidc_base_url:
        parsed = urlsplit(config.oidc_base_url)
        if parsed.hostname and parsed.hostname not in allowed_hosts:
            allowed_hosts.append(parsed.hostname)
        public_origin = f"{parsed.scheme}://{parsed.netloc}"
        if public_origin not in allowed_origins:
            allowed_origins.append(public_origin)

    logger.info(
        "Host/Origin protection enabled (allowed_hosts=%s, allowed_origins=%s)",
        allowed_hosts,
        allowed_origins,
    )
    # Empty lists are passed as None so FastMCP's own defaults still apply; an
    # explicit empty list would be read as "the allow-list is deliberately empty".
    return {
        "host_origin_protection": True,
        "allowed_hosts": allowed_hosts or None,
        "allowed_origins": allowed_origins or None,
    }
