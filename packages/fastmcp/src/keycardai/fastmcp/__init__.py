"""FastMCP integration for Keycard OAuth client.

This module provides seamless integration between Keycard's OAuth client
and FastMCP servers, enabling secure authentication and authorization.

Components:
- AuthProvider: Keycard authentication provider with RemoteAuthProvider creation and grant dependency
- AccessContext: Typed context object for accessing delegated tokens, injected into tool parameters
- Application credentials: ClientSecret, WebIdentity, EKSWorkloadIdentity for different authentication scenarios
- Auth strategies: BasicAuth, MultiZoneBasicAuth, NoneAuth for HTTP client authentication

GrantDependency is the return type of AuthProvider.grant(); it is exported for
type annotations only and is never constructed directly. Testing seams live in
keycardai.fastmcp.testing (mock_access_context is also re-exported here for
backward compatibility).

Re-export Guide:
    Local definitions (primary API): AuthProvider, AccessContext
    From keycardai.mcp.server.auth: ApplicationCredential, ClientSecret, EKSWorkloadIdentity, WebIdentity
    From keycardai.mcp.server.auth.client_factory: ClientFactory, DefaultClientFactory
    From keycardai.oauth.http.auth: AuthStrategy, BasicAuth, MultiZoneBasicAuth, NoneAuth
    From keycardai.mcp.server.exceptions: All exceptions
    For canonical imports, use the source packages directly.

Basic Usage:

    from fastmcp import FastMCP
    from keycardai.fastmcp import AccessContext, AuthProvider

    # Create authentication provider
    auth_provider = AuthProvider(
        zone_id="abc1234",
        mcp_server_name="My Server",
        mcp_base_url="http://localhost:8000"
    )

    # Get the RemoteAuthProvider for FastMCP
    auth = auth_provider.get_remote_auth_provider()
    mcp = FastMCP("My Server", auth=auth)

    # Declare the grant as a typed tool parameter for token exchange
    @mcp.tool()
    async def call_external_api(
        query: str,
        access: AccessContext = auth_provider.grant("https://api.example.com"),
    ):
        token = access.access("https://api.example.com").access_token
        # Use token to call external API
        return f"Results for {query}"

Advanced Configuration:

    # With custom authentication (production)
    from keycardai.fastmcp import ClientSecret

    auth_provider = AuthProvider(
        zone_id="abc1234",
        mcp_server_name="Production Server",
        mcp_base_url="https://my-server.com",
        application_credential=ClientSecret(("client_id", "client_secret"))
    )

    # Multiple resource access
    @mcp.tool()
    async def sync_calendar_to_drive(
        access: AccessContext = auth_provider.grant([
            "https://www.googleapis.com/calendar/v3",
            "https://www.googleapis.com/drive/v3",
        ]),
    ):
        calendar_token = access.access("https://www.googleapis.com/calendar/v3").access_token
        drive_token = access.access("https://www.googleapis.com/drive/v3").access_token
        # Use both tokens for cross-service operations
        return "Sync completed"

    # Multi-zone support
    from keycardai.fastmcp import ClientSecret

    auth_provider = AuthProvider(
        zone_url="https://keycard.cloud",
        mcp_base_url="https://my-server.com",
        application_credential=ClientSecret({
            "https://zone1.keycard.cloud": ("id1", "secret1"),
            "https://zone2.keycard.cloud": ("id2", "secret2"),
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

from .provider import (
    AccessContext,
    AuthProvider,
    GrantDependency,
)
from .testing import mock_access_context

__all__ = [
    # === Primary API (Local Definitions) ===
    "AuthProvider",
    "AccessContext",
    # === Typing Support ===
    # Return type of AuthProvider.grant(); exported for annotations, never constructed directly
    "GrantDependency",
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
