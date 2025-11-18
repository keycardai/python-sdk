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
    # Core primitives
    "Client",
    "ClientManager",
    "Context",
    # Storage
    "StorageBackend",
    "InMemoryBackend",
    "SQLiteBackend",
    "NamespacedStorage",
    # Auth coordination
    "AuthCoordinator",
    "LocalAuthCoordinator",
    "StarletteAuthCoordinator",
    # Auth strategies
    "AuthStrategy",
    "OAuthStrategy",
    "ApiKeyStrategy",
    "NoAuthStrategy",
    "create_auth_strategy",
    # Types
    "AuthChallenge",
    "ToolInfo",
    # Exceptions
    "MCPClientError",
    "ClientConfigurationError",
    # Logging
    "configure_logging",
    "get_logger",
    # Lower-level primitives (advanced usage)
    "Session",
    "SessionStatus",
    "SessionStatusCategory",
]
