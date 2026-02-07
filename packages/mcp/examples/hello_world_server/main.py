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


def main():
    """Entry point for the MCP server."""
    # Wrap the MCP app with Keycard authentication
    app = auth_provider.app(mcp)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
