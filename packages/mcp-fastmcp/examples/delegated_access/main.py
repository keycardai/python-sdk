"""GitHub API Integration with Keycard Delegated Access.

This example demonstrates how to use the @grant decorator to request
token exchange for accessing external APIs (GitHub) on behalf of
authenticated users.

Key concepts demonstrated:
- AuthProvider setup with ClientSecret credentials
- @grant decorator for requesting token exchange
- AccessContext for accessing exchanged tokens
- Comprehensive error handling patterns
"""

import os

import httpx
from fastmcp import Context, FastMCP

from keycardai.mcp.integrations.fastmcp import AccessContext, AuthProvider, ClientSecret

# Configure Keycard authentication with client credentials for delegated access
# Get your zone_id and client credentials from console.keycard.ai
auth_provider = AuthProvider(
    zone_id=os.getenv("KEYCARD_ZONE_ID", "your-zone-id"),
    mcp_server_name="GitHub API Server",
    mcp_base_url=os.getenv("MCP_SERVER_URL", "http://localhost:8000/"),
    # ClientSecret enables token exchange for delegated access
    application_credential=ClientSecret(
        (
            os.getenv("KEYCARD_CLIENT_ID", "your-client-id"),
            os.getenv("KEYCARD_CLIENT_SECRET", "your-client-secret"),
        )
    ),
)

# Get the RemoteAuthProvider for FastMCP
auth = auth_provider.get_remote_auth_provider()

# Create authenticated FastMCP server
mcp = FastMCP("GitHub API Server", auth=auth)


@mcp.tool()
@auth_provider.grant("https://api.github.com")
async def get_github_user(ctx: Context) -> dict:
    """Get the authenticated GitHub user's profile.

    Demonstrates:
    - Basic @grant decorator usage
    - Error checking with has_errors()
    - Token access via AccessContext

    Args:
        ctx: FastMCP context with Keycard authentication state

    Returns:
        User profile data or error details
    """
    # Get access context from FastMCP context namespace
    access_context: AccessContext = ctx.get_state("keycardai")

    # Check for any errors (global or resource-specific)
    if access_context.has_errors():
        errors = access_context.get_errors()
        return {"error": "Token exchange failed", "details": errors}

    # Get the exchanged token for GitHub API
    token = access_context.access("https://api.github.com").access_token

    # Call GitHub API with delegated token
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        if response.status_code != 200:
            return {
                "error": f"GitHub API error: {response.status_code}",
                "details": response.text,
            }

        user_data = response.json()
        return {
            "login": user_data.get("login"),
            "name": user_data.get("name"),
            "email": user_data.get("email"),
            "public_repos": user_data.get("public_repos"),
            "followers": user_data.get("followers"),
        }


@mcp.tool()
@auth_provider.grant("https://api.github.com")
async def list_github_repos(ctx: Context, per_page: int = 5) -> dict:
    """List the authenticated user's GitHub repositories.

    Demonstrates:
    - Resource-specific error checking with has_resource_error()
    - Getting resource-specific errors with get_resource_errors()
    - Parameterized API calls

    Args:
        ctx: FastMCP context with Keycard authentication state
        per_page: Number of repositories to return (default: 5)

    Returns:
        List of repositories or error details
    """
    access_context: AccessContext = ctx.get_state("keycardai")

    # Check for resource-specific error (alternative to has_errors())
    if access_context.has_resource_error("https://api.github.com"):
        resource_errors = access_context.get_resource_errors("https://api.github.com")
        return {
            "error": "Token exchange failed for GitHub API",
            "resource_errors": resource_errors,
        }

    # Check for global errors (e.g., no auth token available)
    if access_context.has_error():
        return {"error": "Global token error", "details": access_context.get_error()}

    token = access_context.access("https://api.github.com").access_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            params={"per_page": per_page, "sort": "updated"},
        )

        if response.status_code != 200:
            return {
                "error": f"GitHub API error: {response.status_code}",
                "details": response.text,
            }

        repos = response.json()
        return {
            "count": len(repos),
            "repositories": [
                {
                    "name": repo.get("name"),
                    "full_name": repo.get("full_name"),
                    "private": repo.get("private"),
                    "html_url": repo.get("html_url"),
                }
                for repo in repos
            ],
        }


def main():
    """Entry point for the MCP server."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
