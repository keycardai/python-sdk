"""FastMCP integration for Keycard OAuth client.

This module provides seamless integration between Keycard's OAuth client
and FastMCP servers, enabling secure authentication and authorization.

Components:
- AuthProvider: Keycard authentication provider with RemoteAuthProvider creation and grant decorator
- AccessContext: Context object for accessing delegated tokens (used in FastMCP Context namespace)
- Auth strategies: BasicAuth, MultiZoneBasicAuth, NoneAuth for different authentication scenarios

Basic Usage:

    from fastmcp import FastMCP, Context
    from keycardai.mcp.integrations.fastmcp import AuthProvider

    # Create authentication provider
    auth_provider = AuthProvider(
        zone_id="abc1234",
        mcp_server_name="My Server",
        mcp_server_url="http://localhost:8000"
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
    from keycardai.mcp.integrations.fastmcp import BasicAuth

    auth_provider = AuthProvider(
        zone_id="abc1234",
        mcp_server_name="Production Server",
        mcp_server_url="https://my-server.com",
        auth=BasicAuth("client_id", "client_secret")
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
    from keycardai.mcp.integrations.fastmcp import MultiZoneBasicAuth

    auth_provider = AuthProvider(
        zone_url="https://keycard.cloud",
        mcp_server_url="https://my-server.com",
        auth=MultiZoneBasicAuth({
            "tenant1": ("id1", "secret1"),
            "tenant2": ("id2", "secret2"),
        })
    )
"""

from keycardai.mcp.server.auth.client_factory import ClientFactory, DefaultClientFactory
from keycardai.mcp.server.exceptions import (
    # Specific exceptions
    AuthProviderConfigurationError,
    ClientInitializationError,
    JWKSValidationError,
    # Base exception
    MCPServerError,
    MetadataDiscoveryError,
    MissingContextError,
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
    # Core classes
    "AuthProvider",
    "AccessContext",

    # Client factory
    "ClientFactory",
    "DefaultClientFactory",

    # Auth strategies
    "AuthStrategy",
    "BasicAuth",
    "MultiZoneBasicAuth",
    "NoneAuth",

    # Exceptions - Base
    "MCPServerError",

    # Exceptions - Specific
    "AuthProviderConfigurationError",
    "ClientInitializationError",
    "JWKSValidationError",
    "MissingContextError",
    "ResourceAccessError",
    "TokenExchangeError",
    "MetadataDiscoveryError",

    # Testing utilities
    "mock_access_context",
]
