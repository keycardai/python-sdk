"""
Google Calendar MCP Server with Delegated Token Exchange

This example demonstrates how to use the unified KeyCard OAuth client
to perform delegated token exchange and access Google Calendar API
on behalf of authenticated users.

Key features:
- Token exchange for Google Calendar access
- Direct Google Calendar API integration
- Proper error handling and data validation
- Clean, modular tool implementation
"""

from typing import Optional

from pydantic import AnyHttpUrl
from fastmcp import Context, FastMCP
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from keycardai.oauth import Client

from .config import get_config
from .tools import get_calendar_events

# Load configuration
config = get_config()

# Configure JWT token verification for your identity provider
token_verifier = JWTVerifier(
    jwks_uri=config.jwks_uri,
    issuer=config.issuer,
    audience=config.audience,
)

# Create the remote auth provider
auth = RemoteAuthProvider(
    token_verifier=token_verifier,
    authorization_servers=[AnyHttpUrl(config.issuer)],
    resource_server_url=config.resource_server_url,
)

# Initialize MCP server
mcp = FastMCP(name="DelegatedTokenExchange", auth=auth)

# Initialize unified OAuth client - client uses dynamica client registration
oauth_client = Client(
    base_url=config.sts_base_url
)

# Register Google Calendar tools
@mcp.tool()
async def get_calendar_events_tool(
    ctx: Context,
    maxResults: int = 10,
    timeMin: Optional[str] = None,
    timeMax: Optional[str] = None,
    calendarId: str = "primary"
):
    """Get Google Calendar events for the authenticated user.

    Uses delegated token exchange to obtain Google Calendar access on behalf
    of the authenticated user, then fetches their calendar events.

    Args:
        ctx: Request context containing user authentication
        maxResults: Maximum number of events to return (1-50, default: 10)
        timeMin: Start time filter in ISO 8601 format (default: now)
        timeMax: End time filter in ISO 8601 format (default: 7 days from now)
        calendarId: Calendar identifier (default: "primary")

    Returns:
        Dictionary containing calendar events and metadata
    """
    return await get_calendar_events(oauth_client, ctx, maxResults, timeMin, timeMax, calendarId)


def main():
    """Entry point for running the MCP server."""
    mcp.run(
        transport="http",
        port=config.port,
        host=config.host,
        path="/mcp"
    )


if __name__ == "__main__":
    main()
