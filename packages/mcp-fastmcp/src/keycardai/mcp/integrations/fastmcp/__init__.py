"""FastMCP integration for KeyCard OAuth client.

This module provides seamless integration between KeyCard's OAuth client
and FastMCP servers, enabling secure authentication and authorization.

Components:
- AuthProvider: KeyCard authentication provider with RemoteAuthProvider creation and grant decorator
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

# Re-export commonly used auth strategies for convenience
from keycardai.oauth.http.auth import (
    AuthStrategy,
    BasicAuth,
    MultiZoneBasicAuth,
    NoneAuth,
)

from .exceptions import (
    # Convenience aliases
    AccessError,
    # Specific exceptions
    AuthProviderConfigurationError,
    ClientInitializationError,
    ConfigurationError,
    DecoratorError,
    DiscoveryError,
    ExchangeError,
    # Base exception
    FastMCPIntegrationError,
    InitializationError,
    JWKSValidationError,
    ResourceAccessError,
    TokenExchangeError,
    ZoneDiscoveryError,
)
from .provider import AccessContext, AuthProvider

__all__ = [
    # Core classes
    "AuthProvider",
    "AccessContext",

    # Auth strategies
    "AuthStrategy",
    "BasicAuth",
    "MultiZoneBasicAuth",
    "NoneAuth",

    # Exceptions - Base
    "FastMCPIntegrationError",

    # Exceptions - Specific
    "AuthProviderConfigurationError",
    "ClientInitializationError",
    "DecoratorError",
    "JWKSValidationError",
    "ResourceAccessError",
    "TokenExchangeError",
    "ZoneDiscoveryError",

    # Exceptions - Convenience aliases
    "AccessError",
    "ConfigurationError",
    "DecoratorError",
    "DiscoveryError",
    "ExchangeError",
    "InitializationError",
]
