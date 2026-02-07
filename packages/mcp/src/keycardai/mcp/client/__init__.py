"""Keycard MCP Client.

This module provides the MCP client for connecting to MCP servers with OAuth authentication.

Primary API:
    Client: High-level client for MCP operations
    ClientManager: Manage multiple client instances
    Context: Client context with auth state

Advanced API (for custom implementations):
    Session, SessionStatus, SessionStatusCategory: Low-level session management
    AuthCoordinator subclasses: Custom auth coordination
"""

from .auth.coordinators import (
    AuthCoordinator,
    LocalAuthCoordinator,
    StarletteAuthCoordinator,
)
from .auth.strategies import (
    ApiKeyStrategy,
    AuthStrategy,
    NoAuthStrategy,
    OAuthStrategy,
    create_auth_strategy,
)
from .client import Client
from .context import Context
from .exceptions import ClientConfigurationError, MCPClientError
from .logging_config import configure_logging, get_logger
from .manager import ClientManager
from .session import Session, SessionStatus, SessionStatusCategory
from .storage import InMemoryBackend, NamespacedStorage, SQLiteBackend, StorageBackend
from .types import AuthChallenge, ToolInfo

__all__ = [
    # === Primary API ===
    "Client",
    "ClientManager",
    "Context",
    # === Storage ===
    "StorageBackend",
    "InMemoryBackend",
    "SQLiteBackend",
    "NamespacedStorage",
    # === Auth Coordination ===
    "AuthCoordinator",
    "LocalAuthCoordinator",
    "StarletteAuthCoordinator",
    # === Auth Strategies ===
    "AuthStrategy",
    "OAuthStrategy",
    "ApiKeyStrategy",
    "NoAuthStrategy",
    "create_auth_strategy",
    # === Types ===
    "AuthChallenge",
    "ToolInfo",
    # === Exceptions ===
    "MCPClientError",  # Base exception for MCP client errors
    "ClientConfigurationError",
    # === Logging ===
    "configure_logging",
    "get_logger",
    # === Advanced (Low-level Session Management) ===
    # Use these only when building custom MCP client implementations
    "Session",
    "SessionStatus",
    "SessionStatusCategory",
]
