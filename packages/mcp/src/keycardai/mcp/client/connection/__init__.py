"""Connection module for MCP client."""

from .base import Connection, ConnectionError
from .factory import create_connection
from .http import StreamableHttpConnection

__all__ = [
    # Base
    "Connection",
    "ConnectionError",
    # Implementations
    "StreamableHttpConnection",
    # Factory
    "create_connection",
]

