"""Authentication provider selection for the MCP server.

Kept separate from server.py so the decision here is a pure function of the
transport config and can be tested without importing the server module.
"""

import logging
from typing import Any

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
