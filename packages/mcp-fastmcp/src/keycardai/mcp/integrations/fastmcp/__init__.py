"""FastMCP integration for KeyCard OAuth client.

This module provides seamless integration between KeyCard's OAuth client
and FastMCP servers, following the sync/async API design standard.

Components:
- KeycardAuthProvider: FastMCP authentication provider using KeyCard zone tokens
- OAuthClientMiddleware: Middleware that manages OAuth client lifecycle
- keycardai: Decorators for automated token exchange in FastMCP tools
"""

from .decorators import keycardai
from .middleware import OAuthClientMiddleware
from .provider import KeycardAuthProvider

__version__ = "0.0.1"

__all__ = [
    "__version__",
    "KeycardAuthProvider",
    "OAuthClientMiddleware",
    "keycardai",
]
