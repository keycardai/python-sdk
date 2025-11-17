"""
Connection factory for creating appropriate connection types.
"""
from typing import TYPE_CHECKING, Any

from ..context import Context
from ..logging_config import get_logger
from .http import StreamableHttpConnection

if TYPE_CHECKING:
    from ..auth.coordinators.base import AuthCoordinator
    from ..storage import NamespacedStorage
    from .base import Connection

logger = get_logger(__name__)


def create_connection(
    server_name: str,
    server_config: dict[str, Any],
    context: Context,
    coordinator: "AuthCoordinator",
    server_storage: "NamespacedStorage"
) -> "Connection | None":
    """
    Factory function to create appropriate connection type.

    Args:
        server_name: Name of the server
        server_config: Server configuration
        context: Context for identity (not for storage - use server_storage)
        coordinator: Auth coordinator for callbacks
        server_storage: Pre-scoped storage namespace for this server

    Returns:
        Connection instance or None if transport not supported
    """
    transport = server_config.get("transport", "http")

    if transport == "http" or server_config.get("url"):
        return StreamableHttpConnection(
            server_name, server_config, context, coordinator, server_storage
        )

    # Future: Add stdio, WebSocket, etc.
    logger.warning(f"Unsupported transport type: {transport}")
    return None

