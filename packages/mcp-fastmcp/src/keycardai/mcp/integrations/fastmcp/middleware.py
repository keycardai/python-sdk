"""Access middleware for FastMCP.

This module provides AccessMiddleware, which manages the lifecycle of
KeyCard's OAuth client and provides automated token exchange through
the grant decorator.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.utilities.logging import get_logger
from pydantic_settings import BaseSettings

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.types.models import TokenResponse

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext

logger = get_logger(__name__)


class AccessMiddlewareSettings(BaseSettings):
    """Settings for access middleware."""
    # OAuth client configuration
    zone_url: str | None = None
    client_name: str | None = None


class AccessMiddleware(Middleware):
    """Middleware that manages OAuth client lifecycle and provides automated token exchange.

    This middleware initializes and manages a KeyCard OAuth client and provides
    the grant decorator for automated token exchange operations. It follows the
    FastMCP middleware protocol and integrates with the context system.

    Features:
    - Lazy initialization of OAuth client on first tool call
    - Automatic client registration and metadata discovery
    - grant() decorator for automated token exchange
    - Support for single or multiple resource access
    - Thread-safe initialization with async locking
    - Integration with FastMCP context state system

    Example:
        ```python
        from fastmcp import FastMCP
        from keycardai.mcp.integrations.fastmcp import AccessMiddleware

        mcp = FastMCP("My Service")
        access = AccessMiddleware(
            zone_url="https://abc1234.keycard.cloud",
            client_name="My MCP Service"
        )
        mcp.add_middleware(access)

        @mcp.tool()
        @access.grant("https://www.googleapis.com/calendar/v3")
        async def my_tool(ctx: Context):
            # ctx.get_state("keycardai").access() provides token responses
            from keycardai.oauth.utils.bearer import create_auth_header
            token = ctx.get_state("keycardai").access("https://www.googleapis.com/calendar/v3").access_token
            headers = {"Authorization": create_auth_header(token)}
        ```
    """

    def __init__(
        self,
        *,
        zone_url: str | None = None,
        client_name: str | None = None,
        client: AsyncClient | None = None,

    ):
        """Initialize access middleware.

        Args:
            zone_url: OAuth server zone URL (from environment if not provided)
            client_name: OAuth client name for registration
        """
        settings = AccessMiddlewareSettings.model_validate({
            "zone_url": zone_url,
            "client_name": client_name,
        })

        self.zone_url = settings.zone_url
        if not self.zone_url:
            raise ValueError(
                "zone_url is required"
            )

        self.client_name = settings.client_name or "FastMCP OAuth Client"

        self.client: AsyncClient | None = client
        self._init_lock: asyncio.Lock | None = None
        self._access_tokens: dict[str, str] = {}

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
        """Ensure OAuth client is available for token exchange operations.

        This method is called before every tool execution and ensures that:
        1. OAuth client is properly initialized for internal use
        2. Ready to handle token exchange requests via grant decorator

        Args:
            context: Middleware context containing the tool call
            call_next: Next middleware or tool handler in the chain

        Returns:
            Result from the next handler in the chain
        """
        await self._ensure_client_initialized()

        return await call_next(context)

    def grant(self, resources: str | list[str]):
        """Decorator for automatic delegated token exchange.

        This decorator automates the OAuth token exchange process for accessing
        external resources on behalf of authenticated users. It:

        1. Extracts the user's bearer token from the FastMCP context
        2. Performs RFC 8693 token exchange for the specified resource(s)
        3. Makes tokens available through ctx.keycardadi_access()
        4. Handles all error cases gracefully

        Args:
            resources: Target resource URL(s) for token exchange.
                      Can be a single string or list of strings.
                      (e.g., "https://www.googleapis.com/calendar/v3" or
                       ["https://www.googleapis.com/calendar/v3", "https://www.googleapis.com/drive/v3"])

        Usage:
            ```python
            @access.grant("https://www.googleapis.com/calendar/v3")
            async def get_calendar_events(ctx: Context, ...) -> dict:
                # Access token available through context accessor
                from keycardai.oauth.utils.bearer import create_auth_header
                token = ctx.get_state("keycardai").access("https://www.googleapis.com/calendar/v3").access_token
                headers = {"Authorization": create_auth_header(token)}
                # Use headers to call Google Calendar API
                ...
            ```

        The decorated function receives:
        - Enhanced context with keycardai namespace available via ctx.get_state("keycardai")
        - All original function parameters unchanged

        Error handling:
        - Returns structured error response if token exchange fails
        - Preserves original function signature and behavior
        - Provides detailed error messages for debugging
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                try:
                    ctx = None
                    for arg in args:
                        if isinstance(arg, Context):
                            ctx = arg
                            break
                    if ctx is None:
                        for _key, value in kwargs.items():
                            if isinstance(value, Context):
                                ctx = value
                                break
                    if ctx is None:
                        return {
                            "error": "No Context parameter found in function arguments.",
                            "isError": True,
                            "errorType": "missing_context",
                        }

                    user_token = get_access_token()
                    if not user_token:
                        return {
                            "error": "No authentication token available. Please ensure you're properly authenticated.",
                            "isError": True,
                            "errorType": "authentication_required",
                        }

                    if self.client is None:
                        return {
                            "error": "OAuth client not available. Server configuration issue.",
                            "isError": True,
                            "errorType": "server_configuration",
                        }

                    resource_list = [resources] if isinstance(resources, str) else resources

                    access_tokens = {}
                    for resource in resource_list:
                        try:
                            token_response = await self.client.exchange_token(
                                subject_token=user_token.token,
                                resource=resource,
                                subject_token_type="urn:ietf:params:oauth:token-type:access_token"
                            )
                            access_tokens[resource] = token_response
                        except Exception as e:
                            return {
                                "error": f"Token exchange failed for {resource}: {e}",
                                "isError": True,
                                "errorType": "exchange_token_failed",
                                "resource": resource,
                            }

                    keycardai_namespace = KeycardaiNamespace(access_tokens)
                    ctx.set_state("keycardai", keycardai_namespace)

                    return await func(*args, **kwargs)

                except Exception as e:
                    return {
                        "error": f"Unexpected error in delegated token exchange: {e}",
                        "isError": True,
                        "errorType": "unexpected_error",
                        "resources": resource_list if 'resource_list' in locals() else resources,
                    }

            return wrapper
        return decorator


class KeycardaiNamespace:
    """Namespace object for keycardai access methods."""

    def __init__(self, access_tokens: dict[str, TokenResponse]):
        self._access_tokens = access_tokens

    def access(self, resource: str) -> TokenResponse:
        """Get token response for the specified resource.
        Args:
            resource: The resource URL to get token response for
        Returns:
            TokenResponse object with access_token attribute
        Raises:
            KeyError: If resource was not granted in the decorator
        """
        if resource not in self._access_tokens:
            raise KeyError(f"Resource '{resource}' not granted. Available resources: {list(self._access_tokens.keys())}")
        return self._access_tokens[resource]


