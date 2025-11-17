"""Type definitions for MCP client public interfaces."""

from typing import NamedTuple, NotRequired, TypedDict

from mcp import Tool


class AuthChallenge(TypedDict):
    """
    Authentication challenge details for a server requiring authentication.

    This is returned when a server requires authentication that hasn't been completed yet.
    The exact fields present depend on the authentication strategy configured for the server.

    All challenges include:
        - server: The name of the server requiring authentication

    Strategy-specific fields may include (but are not limited to):
        - authorization_url: For browser-based auth flows
        - state: For CSRF protection in auth flows
        - Additional fields as defined by the authentication strategy

    Example:
        >>> challenges = await client.get_auth_challenges()
        >>> for challenge in challenges:
        ...     print(f"Server {challenge['server']} needs auth")
        ...     # Check for strategy-specific fields
        ...     if 'authorization_url' in challenge:
        ...         print(f"Open: {challenge['authorization_url']}")
    """
    server: str  # Always present - the server name requiring auth
    authorization_url: NotRequired[str]  # URL for browser-based auth flows
    state: NotRequired[str]  # CSRF protection token for auth flows
    # Additional strategy-specific fields may be present as NotRequired


class ToolInfo(NamedTuple):
    """
    Information about a tool and which server provides it.

    This type makes it explicit which server a tool belongs to,
    useful when you need to track tool provenance across multiple servers.

    Attributes:
        tool: The MCP Tool object with name, description, and input schema
        server: The name of the server that provides this tool

    Example:
        >>> tools = await client.list_tools_with_servers()
        >>> for info in tools:
        ...     print(f"{info.tool.name} (from {info.server})")
        ...     # Access tool details:
        ...     print(f"  Description: {info.tool.description}")
    """
    tool: Tool
    server: str


__all__ = [
    "AuthChallenge",
    "ToolInfo",
]

