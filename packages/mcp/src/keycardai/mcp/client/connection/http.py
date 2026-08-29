"""
HTTP connection implementation for MCP client.

Provides StreamableHttpConnection which uses httpx2 for HTTP-based MCP connections.
"""
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

import httpx2
from mcp.client.streamable_http import TransportStreams, streamable_http_client

from ..auth.strategies import create_auth_strategy
from ..auth.transports import HttpxAuth
from ..context import Context
from ..logging_config import get_logger
from .base import Connection

if TYPE_CHECKING:
    from ..auth.coordinators.base import AuthCoordinator
    from ..storage import NamespacedStorage

logger = get_logger(__name__)

MCP_DEFAULT_TIMEOUT = 30.0
MCP_DEFAULT_SSE_READ_TIMEOUT = 300.0


class StreamableHttpConnection(Connection):
    """
    HTTP-based MCP connection with per-server authentication.

    Each connection instance represents a connection to a single server
    with its own isolated auth strategy and storage.
    """

    def __init__(
        self,
        server_name: str,
        server_config: dict[str, Any],
        context: Context,
        coordinator: "AuthCoordinator",
        server_storage: "NamespacedStorage"
    ):
        """
        Initialize HTTP connection.

        Args:
            server_name: Name of the server (for logging)
            server_config: Server configuration including URL and auth config
            context: Context providing identity (not used for storage)
            coordinator: Auth coordinator for OAuth callbacks
            server_storage: Pre-scoped storage namespace for this server
                          (created by Session as server:{server_name})
        """
        super().__init__()
        self.server_name = server_name
        self.server_config = server_config
        self.context = context
        self.coordinator = coordinator
        self._mcp_client: (
            AbstractAsyncContextManager[TransportStreams] | None
        ) = None
        self._http_client: httpx2.AsyncClient | None = None
        self._disconnecting = False

        # Create connection-specific sub-namespace
        self.storage = server_storage.get_namespace("connection")

        # Create auth strategy for this server
        # Strategy will create its own sub-namespace (oauth:, api_key:, etc.)
        auth_config = server_config.get("auth")
        self.auth_strategy = create_auth_strategy(
            auth_config,
            server_name,
            connection_storage=self.storage,
            context=context,
            coordinator=coordinator
        )

        logger.debug(
            f"Created connection for '{server_name}' with strategy: "
            f"{self.auth_strategy.__class__.__name__}"
        )

    async def connect(self) -> tuple[Any, Any]:
        """Establish HTTP connection with auth adapter."""
        url = self.server_config.get("url")
        if not isinstance(url, str):
            raise ValueError("HTTP server configuration requires a URL")

        auth = HttpxAuth(strategy=self.auth_strategy)
        self._http_client = httpx2.AsyncClient(
            auth=auth,
            follow_redirects=True,
            timeout=httpx2.Timeout(
                MCP_DEFAULT_TIMEOUT,
                read=MCP_DEFAULT_SSE_READ_TIMEOUT,
            ),
        )

        self._mcp_client = streamable_http_client(
            url,
            http_client=self._http_client,
        )
        try:
            self._read_stream, self._write_stream = (
                await self._mcp_client.__aenter__()
            )
            return (self._read_stream, self._write_stream)
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnect from HTTP server."""
        if self._disconnecting or (
            self._mcp_client is None and self._http_client is None
        ):
            return

        self._disconnecting = True

        try:
            if self._mcp_client is not None:
                try:
                    await self._mcp_client.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Error during disconnect (suppressed): {e}")

            if self._http_client is not None:
                try:
                    await self._http_client.aclose()
                except Exception as e:
                    logger.debug(
                        f"Error closing HTTP client (suppressed): {e}"
                    )
        finally:
            self._mcp_client = None
            self._http_client = None
            self._disconnecting = False
