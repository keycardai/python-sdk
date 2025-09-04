"""FastMCP integration for KeyCard OAuth client.

This module provides seamless integration between KeyCard's OAuth client
and FastMCP servers, following the sync/async API design standard.

Components:
- KeycardAuthProvider: FastMCP authentication provider using KeyCard zone tokens
- OAuthClientMiddleware: Middleware that manages OAuth client lifecycle
- keycardai: Decorators for automated token exchange in FastMCP tools

Example Usage:

    # Basic FastMCP server setup with KeyCard authentication
    import fastmcp
    from keycardai.mcp.integrations.fastmcp import (
        KeycardAuthProvider,
        OAuthClientMiddleware,
        keycardai
    )

    # Create MCP server with KeyCard authentication
    mcp = fastmcp.MCP("My KeyCard Server")

    # Add authentication provider
    auth_provider = KeycardAuthProvider(
        zone_url="https://my-keycard-zone.com",
        audience="my-mcp-server"
    )
    mcp.add_auth_provider(auth_provider)

    # Add OAuth client middleware for automatic token management
    oauth_middleware = OAuthClientMiddleware(
        zone_url="https://my-keycard-zone.com"
    )
    mcp.add_middleware(oauth_middleware)

    # Use the decorator for automatic token exchange in tools
    @mcp.tool()
    @keycardai.get_access_token_for_resource("https://api.example.com")
    async def call_external_api(query: str) -> str:
        '''Call an external API on behalf of the authenticated user.

        The decorator automatically exchanges the user's token for one
        that can access the specified resource.
        '''
        # The exchanged token is available in the context
        context = fastmcp.get_context()
        delegated_token = context.get("delegated_token")

        # Use the token to call the external API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.example.com/search",
                params={"q": query},
                headers={"Authorization": f"Bearer {delegated_token}"}
            )
            return response.json()

    # Simplified setup for common scenarios
    @mcp.tool()
    @keycardai.get_access_token_for_resource("calendar")
    async def get_calendar_events() -> list:
        '''Get user's calendar events with automatic token delegation.'''
        context = fastmcp.get_context()
        token = context.get("delegated_token")

        # Calendar API call with delegated token
        # ... implementation details ...
        return events

    # Run the server
    if __name__ == "__main__":
        mcp.run()

Advanced Configuration:

    # Custom OAuth client configuration
    oauth_middleware = OAuthClientMiddleware(
        zone_url="https://my-keycard-zone.com",
        client_id="custom-client-id",  # Optional: auto-discovery if not provided
        timeout=30.0,
        auto_register_client=True,
        enable_metadata_discovery=True
    )

    # Custom authentication with specific requirements
    auth_provider = KeycardAuthProvider(
        zone_url="https://my-keycard-zone.com",
        audience="my-specific-audience",
        mcp_server_name="My Custom Server",
        resource_server_url="https://my-resource-server.com/"
    )

    # Multiple resource decorators
    @mcp.tool()
    @keycardai.get_access_token_for_resource("google-calendar")
    @keycardai.get_access_token_for_resource("google-drive")
    async def sync_calendar_to_drive():
        '''Sync calendar events to Google Drive with multiple token exchanges.'''
        context = fastmcp.get_context()
        calendar_token = context.get("delegated_token_google-calendar")
        drive_token = context.get("delegated_token_google-drive")

        # Use both tokens for cross-service operations
        # ... implementation ...
"""

from .decorators import get_access_token_for_resource
from .middleware import OAuthClientMiddleware
from .provider import KeycardAuthProvider

__version__ = "0.0.1"

__all__ = [
    "__version__",
    "KeycardAuthProvider",
    "OAuthClientMiddleware",
    "get_access_token_for_resource",
]
