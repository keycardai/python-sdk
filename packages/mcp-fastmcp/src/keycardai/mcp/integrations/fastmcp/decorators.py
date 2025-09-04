"""KeyCard MCP decorators for automated token exchange.

This module provides decorators that automate OAuth token exchange for accessing
external resources on behalf of authenticated users.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from fastmcp import Context
from fastmcp.server.dependencies import get_access_token


def request_access_for_resource(resource: str):
    """Decorator for automatic delegated token exchange.

    This decorator automates the OAuth token exchange process for accessing
    external resources on behalf of authenticated users. It:

    1. Extracts the user's bearer token from the FastMCP context
    2. Performs RFC 8693 token exchange for the specified resource
    3. Injects the resource-specific access token into the function context
    4. Handles all error cases gracefully

    Args:
        resource: Target resource URL for token exchange
                 (e.g., "https://www.googleapis.com/calendar/v3")

    Usage:
        ```python
        @keycardai.request_access_for_resource("https://www.googleapis.com/calendar/v3")
        async def get_calendar_events(ctx: Context, ...) -> dict:
            # ctx.access_token is now available with Google Calendar access
            access_token = ctx.access_token
            # Use access_token to call Google Calendar API
            ...
        ```

    The decorated function receives:
    - ctx.access_token: Resource-specific access token
    - All original function parameters unchanged

    Error handling:
    - Returns structured error response if token exchange fails
    - Preserves original function signature and behavior
    - Provides detailed error messages for debugging
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(ctx: Context, *args, **kwargs) -> Any:
            try:
                # Extract user token from FastMCP context
                user_token = get_access_token()
                if not user_token:
                    return {
                        "error": "No authentication token available. Please ensure you're properly authenticated.",
                        "isError": True,
                        "errorType": "authentication_required",
                    }

                # Get OAuth client from context state (set by OAuthClientMiddleware)
                client = ctx.get_state("oauth_client")
                if client is None:
                    return {
                        "error": "OAuth client not available. Server configuration issue.",
                        "isError": True,
                        "errorType": "server_configuration",
                    }

                # Perform token exchange for the specified resource
                try:
                    access_token = await client.access_token_for_resource(resource, user_token.token)
                except Exception as e:
                    return {
                        "error": f"Token exchange failed: {e}",
                        "isError": True,
                        "errorType": "token_exchange_failed",
                        "resource": resource,
                    }

                # Inject the access token into the context for the function to use
                # Create a new context object with the access token
                class ContextWithToken:
                    """Context wrapper that adds access_token attribute."""
                    def __init__(self, original_ctx: Context, access_token: str):
                        self._original_ctx = original_ctx
                        self.access_token = access_token

                    def __getattr__(self, name):
                        """Delegate all other attributes to the original context."""
                        return getattr(self._original_ctx, name)

                enhanced_ctx = ContextWithToken(ctx, access_token)

                # Call the original function with the enhanced context
                return await func(enhanced_ctx, *args, **kwargs)

            except Exception as e:
                return {
                    "error": f"Unexpected error in delegated token exchange: {e}",
                    "isError": True,
                    "errorType": "unexpected_error",
                    "resource": resource,
                }

        return wrapper
    return decorator


# Make the decorator available at the keycardai module level
class KeyCardDecorators:
    """KeyCard decorators namespace."""

    request_access_for_resource = staticmethod(request_access_for_resource)


# This allows the usage: @keycardai.request_access_for_resource(resource)
keycardai = KeyCardDecorators()
