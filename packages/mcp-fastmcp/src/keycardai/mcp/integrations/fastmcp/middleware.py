"""OAuth client middleware for FastMCP.

This module provides OAuthClientMiddleware, which manages the lifecycle of
KeyCard's OAuth client and makes it available to MCP tools through the
FastMCP context system.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.utilities.logging import get_logger
from pydantic_settings import BaseSettings

from keycardai.oauth import AsyncClient, ClientConfig

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext

logger = get_logger(__name__)


class OAuthClientMiddlewareSettings(BaseSettings):
    """Settings for OAuth client middleware."""
    # OAuth client configuration
    zone_url: str | None = None
    client_name: str | None = None


class OAuthClientMiddleware(Middleware):
    """Middleware that manages OAuth client lifecycle and provides it to tools.

    This middleware initializes and manages a KeyCard OAuth client that can be used
    by MCP tools for token exchange operations. It follows the FastMCP middleware
    protocol and integrates with the context system to make the client available
    to tools.

    Features:
    - Lazy initialization of OAuth client on first tool call
    - Automatic client registration and metadata discovery
    - Proper async context management and cleanup
    - Thread-safe initialization with async locking
    - Integration with FastMCP context state system

    Example:
        ```python
        from fastmcp import FastMCP
        from keycardai.mcp.integrations.fastmcp import OAuthClientMiddleware

        mcp = FastMCP("My Service")
        oauth_middleware = OAuthClientMiddleware(
            base_url="https://abc1234.keycard.cloud",
            client_name="My MCP Service"
        )
        mcp.add_middleware(oauth_middleware)

        @mcp.tool()
        async def my_tool(ctx: Context):
            oauth_client = ctx.get_state("oauth_client")
            # Use oauth_client for token exchange...
        ```
    """

    def __init__(
        self,
        *,
        zone_url: str | None = None,
        client_name: str | None = None,
    ):
        """Initialize OAuth client middleware.

        Args:
            base_url: OAuth server base URL (from environment if not provided)
            client_name: OAuth client name for registration
            timeout: HTTP timeout for OAuth operations
            auto_register_client: Whether to automatically register client
            enable_metadata_discovery: Whether to perform metadata discovery
        """
        settings = OAuthClientMiddlewareSettings.model_validate({
            "zone_url": zone_url,
            "client_name": client_name,
        })

        self.zone_url = settings.zone_url
        if not self.zone_url:
            raise ValueError(
                "zone_url is required"
            )

        self.client_name = settings.client_name or "FastMCP OAuth Client"

        self.client: AsyncClient | None = None
        self._init_lock: asyncio.Lock | None = None

    async def _ensure_client_initialized(self):
        """Initialize OAuth client if not already done.

        This method provides middleware-level synchronization to ensure only one
        OAuth client instance is created, even with concurrent requests. The OAuth
        client's own _ensure_initialized() handles discovery and registration.

        Thread-safety: Uses async lock to prevent race conditions between concurrent
        requests that could create multiple client instances.
        """
        if self.client is not None:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            # Double-check: another coroutine might have initialized while we waited
            if self.client is not None:
                return

            try:
                client_config = ClientConfig(
                    client_name=self.client_name,
                    auto_register_client=True,
                    enable_metadata_discovery=True,
                )

                self.client = AsyncClient(
                    base_url=self.zone_url,
                    config=client_config,
                )
            except Exception as e:
                logger.error("Failed to initialize OAuth client: %s", e)
                self.client = None
                raise

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> any:
        """Ensure OAuth client is available for tools that need it.

        This method is called before every tool execution and ensures that:
        1. OAuth client is properly initialized
        2. Client is available in the FastMCP context state
        3. Tools can access it via ctx.get_state("oauth_client")

        Args:
            context: Middleware context containing the tool call
            call_next: Next middleware or tool handler in the chain

        Returns:
            Result from the next handler in the chain
        """
        await self._ensure_client_initialized()

        if context.fastmcp_context and self.client:
            context.fastmcp_context.set_state("oauth_client", self.client)

        return await call_next(context)
