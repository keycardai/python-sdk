"""Exception classes for MCP client.

This module defines all custom exceptions used throughout the MCP client package,
providing clear error types and documentation for different failure scenarios.
"""

from __future__ import annotations


class MCPClientError(Exception):
    """Base exception for all MCP client errors.

    This is the base class for all exceptions raised by the MCP client package.
    It provides a common interface for error handling and allows catching all
    MCP client-related errors with a single except clause.

    Attributes:
        message: Human-readable error message
        details: Optional dictionary with additional error context
    """

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ):
        """Initialize MCP client error.

        Args:
            message: Human-readable error message
            details: Optional dictionary with additional error context
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """Return string representation of the error."""
        return self.message


class ClientConfigurationError(MCPClientError):
    """Raised when Client is misconfigured.

    This exception is raised during Client initialization when
    the provided configuration is invalid or incomplete.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        context_id: str | None = None,
        has_context: bool = False,
        servers_count: int | None = None
    ):
        """Initialize client configuration error with detailed context.

        Args:
            message: Custom error message (optional)
            context_id: Provided context_id value
            has_context: Whether a pre-built context was provided
            servers_count: Number of servers in configuration
        """
        if message is None:
            if context_id and has_context:
                message = (
                    "Cannot provide both 'context_id' and 'context' parameters.\n\n"
                    "These parameters are mutually exclusive:\n"
                    "- 'context_id': Use when you want the Client to create a new context\n"
                    "- 'context': Use when you have a pre-built context from a ClientManager\n\n"
                    "Examples:\n"
                    "  # Option 1: Let Client create context with custom ID\n"
                    "  client = Client(servers=servers, context_id='user:alice')\n\n"
                    "  # Option 2: Use pre-built context from manager\n"
                    "  coordinator = LocalAuthCoordinator()\n"
                    "  context = coordinator.create_context('user:alice')\n"
                    "  client = Client(servers=servers, context=context)\n\n"
                    "Choose one approach, not both."
                )
            else:
                message = (
                    "Client configuration is invalid.\n\n"
                    "Please verify your Client initialization parameters."
                )

        details = {
            "provided_context_id": str(context_id) if context_id else "none",
            "has_pre_built_context": str(has_context),
            "servers_count": str(servers_count) if servers_count is not None else "unknown",
            "solution": "Provide either 'context_id' OR 'context', but not both"
            if context_id and has_context
            else "Review Client initialization parameters",
        }

        super().__init__(message, details=details)


class ToolNotFoundException(MCPClientError):
    """Raised when a requested tool is not found on any server.

    This exception is raised when attempting to call a tool that doesn't exist
    on any of the configured servers. It's typically raised during auto-discovery
    when no server provides the requested tool.
    """

    def __init__(
        self,
        tool_name: str,
        *,
        searched_servers: list[str] | None = None,
        available_tools: list[str] | None = None
    ):
        """Initialize tool not found error.

        Args:
            tool_name: Name of the tool that was not found
            searched_servers: List of server names that were searched
            available_tools: Optional list of available tool names (for suggestions)
        """
        message = f"Tool '{tool_name}' not found on any server"

        if searched_servers:
            servers_str = ", ".join(searched_servers)
            message += f"\n\nSearched servers: {servers_str}"

        if available_tools:
            message += f"\n\nAvailable tools: {', '.join(available_tools)}"
            message += "\n\nTip: Use client.list_tools() to see all available tools."

        details = {
            "tool_name": tool_name,
            "searched_servers": ", ".join(searched_servers) if searched_servers else "unknown",
            "available_tool_count": str(len(available_tools)) if available_tools else "unknown",
        }

        super().__init__(message, details=details)
        self.tool_name = tool_name
        self.searched_servers = searched_servers or []
        self.available_tools = available_tools or []


# Export all exception classes
__all__ = [
    # Base exception
    "MCPClientError",
    # Specific exceptions
    "ClientConfigurationError",
    "ToolNotFoundException",
]

