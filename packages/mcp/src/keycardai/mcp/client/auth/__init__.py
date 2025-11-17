"""Authentication module for MCP client."""

# Import handlers to register built-in handlers
from . import handlers  # noqa: F401
from .coordinators import (
    AuthCoordinator,
    LocalAuthCoordinator,
    StarletteAuthCoordinator,
)
from .events import CompletionEvent, CompletionSubscriber
from .handlers import (
    CompletionHandlerFunc,
    CompletionHandlerRegistry,
    get_default_handler_registry,
    oauth_completion_handler,
    register_completion_handler,
)
from .strategies import (
    ApiKeyStrategy,
    AuthStrategy,
    NoAuthStrategy,
    OAuthStrategy,
    create_auth_strategy,
)
from .transports import HttpxAuth

__all__ = [
    # Coordinators
    "AuthCoordinator",
    "LocalAuthCoordinator",
    "StarletteAuthCoordinator",
    # Strategies
    "AuthStrategy",
    "OAuthStrategy",
    "ApiKeyStrategy",
    "NoAuthStrategy",
    "create_auth_strategy",
    # Adapters
    "HttpxAuth",
    # Observer Pattern
    "CompletionSubscriber",
    "CompletionEvent",
    # Completion Handler Registry
    "CompletionHandlerRegistry",
    "CompletionHandlerFunc",
    "register_completion_handler",
    "get_default_handler_registry",
    "oauth_completion_handler",
]
