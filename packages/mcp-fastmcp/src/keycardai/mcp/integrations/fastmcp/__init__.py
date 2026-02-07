"""FastMCP integration for Keycard OAuth client.

This module provides seamless integration between Keycard's OAuth client
and FastMCP servers, enabling secure authentication and authorization.

Components:
- AuthProvider: Keycard authentication provider with RemoteAuthProvider creation and grant decorator
- AccessContext: Context object for accessing delegated tokens (used in FastMCP Context namespace)
- Application credentials: ClientSecret, WebIdentity, EKSWorkloadIdentity for different authentication scenarios
- Auth strategies: BasicAuth, MultiZoneBasicAuth, NoneAuth for HTTP client authentication

Re-export Guide:
    Local definitions (primary API): AuthProvider, AccessContext
    From keycardai.mcp.server.auth: ApplicationCredential, ClientSecret, EKSWorkloadIdentity, WebIdentity
    From keycardai.mcp.server.auth.client_factory: ClientFactory, DefaultClientFactory
    From keycardai.oauth.http.auth: AuthStrategy, BasicAuth, MultiZoneBasicAuth, NoneAuth
    From keycardai.mcp.server.exceptions: All exceptions
    For canonical imports, use the source packages directly.

Basic Usage:

    from fastmcp import FastMCP, Context
    from keycardai.mcp.integrations.fastmcp import AuthProvider

    # Create authentication provider
    auth_provider = AuthProvider(
        zone_id="abc1234",
        mcp_server_name="My Server",
        mcp_base_url="http://localhost:8000"
    )

    # Get the RemoteAuthProvider for FastMCP
    auth = auth_provider.get_remote_auth_provider()
    mcp = FastMCP("My Server", auth=auth)

    # Use grant decorator for token exchange
    @mcp.tool()
    @auth_provider.grant("https://api.example.com")
    def call_external_api(ctx: Context, query: str):
        token = ctx.get_state("keycardai").access("https://api.example.com").access_token
        # Use token to call external API
        return f"Results for {query}"

Advanced Configuration:

    # With custom authentication (production)
    from keycardai.mcp.integrations.fastmcp import ClientSecret

    auth_provider = AuthProvider(
        zone_id="abc1234",
        mcp_server_name="Production Server",
        mcp_base_url="https://my-server.com",
        application_credential=ClientSecret(("client_id", "client_secret"))
    )

    # Multiple resource access
    @mcp.tool()
    @auth_provider.grant(["https://www.googleapis.com/calendar/v3", "https://www.googleapis.com/drive/v3"])
    async def sync_calendar_to_drive(ctx: Context):
        calendar_token = ctx.get_state("keycardai").access("https://www.googleapis.com/calendar/v3").access_token
        drive_token = ctx.get_state("keycardai").access("https://www.googleapis.com/drive/v3").access_token
        # Use both tokens for cross-service operations
        return "Sync completed"

    # Multi-zone support
    from keycardai.mcp.integrations.fastmcp import ClientSecret

    auth_provider = AuthProvider(
        zone_url="https://keycard.cloud",
        mcp_base_url="https://my-server.com",
        application_credential=ClientSecret({
            "zone1": ("id1", "secret1"),
            "zone2": ("id2", "secret2"),
        })
    )
"""

from keycardai.mcp.server.auth import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from keycardai.mcp.server.auth.client_factory import ClientFactory, DefaultClientFactory
from keycardai.mcp.server.exceptions import (
    # Specific exceptions
    AuthProviderConfigurationError,
    AuthProviderInternalError,
    AuthProviderRemoteError,
    ClientInitializationError,
    EKSWorkloadIdentityConfigurationError,
    EKSWorkloadIdentityRuntimeError,
    JWKSValidationError,
    # Base exception
    MCPServerError,
    MetadataDiscoveryError,
    MissingContextError,
    OAuthClientConfigurationError,
    ResourceAccessError,
    TokenExchangeError,
)
from keycardai.oauth.http.auth import (
    AuthStrategy,
    BasicAuth,
    MultiZoneBasicAuth,
    NoneAuth,
)

from .provider import AccessContext, AuthProvider
from .testing import mock_access_context

__all__ = [
    # === Primary API (Local Definitions) ===
    "AuthProvider",
    "AccessContext",
    # === Application Credentials (re-exported from keycardai.mcp.server.auth) ===
    "ApplicationCredential",
    "ClientSecret",
    "EKSWorkloadIdentity",
    "WebIdentity",
    # === Client Factory (Advanced - re-exported from keycardai.mcp.server.auth) ===
    # Use ClientFactory protocol for custom implementations; DefaultClientFactory for defaults
    "ClientFactory",
    "DefaultClientFactory",
    # === HTTP Auth Strategies (re-exported from keycardai.oauth.http.auth) ===
    "AuthStrategy",
    "BasicAuth",
    "MultiZoneBasicAuth",
    "NoneAuth",
    # === Exceptions (re-exported from keycardai.mcp.server.exceptions) ===
    # Base
    "MCPServerError",
    # Configuration
    "AuthProviderConfigurationError",
    "OAuthClientConfigurationError",
    "EKSWorkloadIdentityConfigurationError",
    "ClientInitializationError",
    # Runtime
    "AuthProviderInternalError",
    "AuthProviderRemoteError",
    "EKSWorkloadIdentityRuntimeError",
    "TokenExchangeError",
    "ResourceAccessError",
    "MissingContextError",
    # Validation
    "JWKSValidationError",
    "MetadataDiscoveryError",
    # === Testing Utilities ===
    "mock_access_context",
]
