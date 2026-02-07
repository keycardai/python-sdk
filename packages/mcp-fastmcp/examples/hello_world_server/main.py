"""Hello World MCP Server with Keycard Authentication.

A minimal example demonstrating FastMCP integration with Keycard OAuth.
"""

import os

from fastmcp import FastMCP

from keycardai.mcp.integrations.fastmcp import AuthProvider

# Configure Keycard authentication
# Get your zone_id from console.keycard.ai
auth_provider = AuthProvider(
    zone_id=os.getenv("KEYCARD_ZONE_ID", "your-zone-id"),
    mcp_server_name="Hello World Server",
    mcp_base_url=os.getenv("MCP_SERVER_URL", "http://localhost:8000/"),
)

# Get the RemoteAuthProvider for FastMCP
auth = auth_provider.get_remote_auth_provider()

# Create authenticated FastMCP server
mcp = FastMCP("Hello World Server", auth=auth)


@mcp.tool()
def hello_world(name: str) -> str:
    """Say hello to an authenticated user.

    Args:
        name: The name to greet

    Returns:
        A personalized greeting message
    """
    return f"Hello, {name}! You are authenticated."


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
