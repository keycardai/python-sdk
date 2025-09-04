"""KeyCard AI MCP SDK

A Python SDK for Model Context Protocol (MCP) functionality that simplifies
authentication and authorization concerns for developers.

Features:
- Simplified MCP server/client authentication
- OAuth 2.0 integration for MCP resources
- Token management for MCP operations
- Security best practices for AI/LLM integrations
"""

# Extend namespace path to include integrations from other packages
__path__ = __import__('pkgutil').extend_path(__path__, __name__)

# Core MCP functionality (to be implemented)
# from .auth import MCPAuthenticator
# from .client import MCPClient
# from .server import MCPServer
# from .exceptions import MCPError, MCPAuthError
# from .types import MCPResource, MCPTool, MCPPrompt

__version__ = "0.0.1"

# Placeholder exports - will be expanded as functionality is implemented
__all__ = [
    "__version__",
    # Core components will be added here
]
