"""Hello World MCP Server with Low-Level Keycard Authentication.

A minimal example demonstrating the keycardai-mcp package's AuthProvider
for scenarios requiring more control than the FastMCP integration.
"""

import os

import uvicorn
from mcp.server.fastmcp import FastMCP

from keycardai.mcp.server.auth import AuthProvider

# Configure Keycard authentication
# Get your zone_id from console.keycard.ai
auth_provider = AuthProvider(
    zone_id=os.getenv("KEYCARD_ZONE_ID", "your-zone-id"),
    mcp_server_name="Hello World Server",
    mcp_server_url=os.getenv("MCP_SERVER_URL", "http://localhost:8000/"),
)

# Create MCP server (not authenticated yet)
mcp = FastMCP("Hello World Server")


@mcp.tool()
def hello_world(name: str) -> str:
    """Say hello to an authenticated user.

    Args:
        name: The name to greet

    Returns:
        A personalized greeting message
    """
    return f"Hello, {name}! You are authenticated."


# Example tool with delegated access (uncomment to use)
# Requires KEYCARD_CLIENT_ID and KEYCARD_CLIENT_SECRET env vars
#
# from keycardai.mcp.server.auth import AccessContext
#
# @mcp.tool()
# @auth_provider.grant("https://api.example.com")
# def get_external_data(access_ctx: AccessContext, query: str) -> str:
#     """Fetch data from an external API using delegated access.
#
#     Note: Low-level MCP uses AccessContext as a function parameter,
#     not retrieved from ctx.get_state().
#
#     Args:
#         access_ctx: AccessContext with exchanged tokens
#         query: Search query for the external API
#
#     Returns:
#         Data from the external API or error message
#     """
#     if access_ctx.has_errors():
#         return f"Token exchange failed: {access_ctx.get_errors()}"
#
#     token = access_ctx.access("https://api.example.com").access_token
#     # Use token to call external API
#     return f"Fetched data for query: {query}"


def main():
    """Entry point for the MCP server."""
    # Wrap the MCP app with Keycard authentication
    app = auth_provider.app(mcp)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
