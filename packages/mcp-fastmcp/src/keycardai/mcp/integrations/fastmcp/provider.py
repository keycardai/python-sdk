"""Keycard authentication provider for FastMCP.

This module provides AuthProvider, which integrates Keycard's OAuth
token verification with FastMCP's authentication system. The AuthProvider
creates a RemoteAuthProvider instance with automatic Keycard zone discovery
and JWT token verification.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic import AnyHttpUrl

from fastmcp import Context
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from keycardai.mcp.server.auth.client_factory import ClientFactory, DefaultClientFactory
from keycardai.mcp.server.exceptions import (
    AuthProviderConfigurationError,
    MetadataDiscoveryError,
    MissingContextError,
    OAuthClientConfigurationError,
    ResourceAccessError,
)
from keycardai.oauth import AsyncClient, Client
from keycardai.oauth.http.auth import AuthStrategy, NoneAuth
from keycardai.oauth.types.models import TokenResponse


class AccessContext:
    """Context object that provides access to exchanged tokens for specific resources.

    Supports both successful token storage and per-resource error tracking,
    allowing partial success scenarios where some resources succeed while others fail.
    """

    def __init__(self, access_tokens: dict[str, TokenResponse] | None = None):
        """Initialize with access tokens for resources.

        Args:
            access_tokens: Dict mapping resource URLs to their TokenResponse objects
        """
        self._access_tokens: dict[str, TokenResponse] = access_tokens or {}
        self._resource_errors: dict[str, dict[str, str]] = {}
        self._error: dict[str, str] | None = None

    def set_bulk_tokens(self, access_tokens: dict[str, TokenResponse]):
        """Set access tokens for resources."""
        self._access_tokens.update(access_tokens)

    def set_token(self, resource: str, token: TokenResponse):
        """Set token for the specified resource."""
        self._access_tokens[resource] = token
        # Clear any previous error for this resource
        self._resource_errors.pop(resource, None)

    def set_resource_error(self, resource: str, error: dict[str, str]):
        """Set error for a specific resource."""
        self._resource_errors[resource] = error
        # Remove token if it exists (error takes precedence)
        self._access_tokens.pop(resource, None)

    def set_error(self, error: dict[str, str]):
        """Set error that affects all resources."""
        self._error = error

    def has_resource_error(self, resource: str) -> bool:
        """Check if a specific resource has an error."""
        return resource in self._resource_errors

    def has_error(self) -> bool:
        """Check if there's a global error."""
        return self._error is not None

    def has_errors(self) -> bool:
        """Check if there are any errors (global or resource-specific)."""
        return self.has_error() or len(self._resource_errors) > 0

    def get_errors(self) -> dict[str, Any] | None:
        """Get global errors if any."""
        return {"resource_errors": self._resource_errors.copy(), "error": self._error}

    def get_error(self) -> dict[str, str] | None:
        """Get global error if any."""
        return self._error

    def get_resource_errors(self, resource: str) -> dict[str, str] | None:
        """Get error for a specific resource."""
        return self._resource_errors.get(resource)

    def get_status(self) -> str:
        """Get overall status of the access context."""
        if self.has_error():
            return "error"
        elif self.has_errors():
            return "partial_error"
        else:
            return "success"

    def get_successful_resources(self) -> list[str]:
        """Get list of resources that have successful tokens."""
        return list(self._access_tokens.keys())

    def get_failed_resources(self) -> list[str]:
        """Get list of resources that have errors."""
        return list(self._resource_errors.keys())

    def access(self, resource: str) -> TokenResponse:
        """Get token response for the specified resource.

        Args:
            resource: The resource URL to get token response for

        Returns:
            TokenResponse object with access_token attribute

        Raises:
            ResourceAccessError: If resource was not granted or has an error
        """
        # Check for global error first
        if self.has_error():
            raise ResourceAccessError()

        # Check for resource-specific error
        if self.has_resource_error(resource):
            raise ResourceAccessError()

        # Check if token exists
        if resource not in self._access_tokens:
            raise ResourceAccessError()

        return self._access_tokens[resource]


class AuthProvider:
    """Keycard authentication provider for FastMCP.

    This provider integrates Keycard's zone-based authentication with FastMCP's
    authentication system. It provides a clean interface for configuring Keycard
    authentication and returns a RemoteAuthProvider instance for FastMCP integration.

    Features:
    - Automatic Keycard zone metadata discovery
    - JWT token verification with JWKS endpoint discovery
    - Configurable OAuth scope requirements
    - RemoteAuthProvider creation for FastMCP integration
    - Support for both zone_id and zone_url configuration
    - Token exchange with grant decorator for delegated access

    Example:
        ```python
        from fastmcp import FastMCP, Context
        from keycardai.mcp.integrations.fastmcp import AuthProvider, AccessContext

        # Using zone_id (recommended)
        auth_provider = AuthProvider(
            zone_id="abc1234",
            mcp_server_name="My FastMCP Service",
            required_scopes=["calendar:read", "drive:read"],
            # The keycard configured resource must have a trailing slash.
            # If trailing slash is not present, it will be added automatically.
            mcp_base_url="http://localhost:8000/"
        )

        # Or using full zone_url
        auth_provider = AuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My FastMCP Service",
            mcp_base_url="http://localhost:8000/"
        )

        # To configure access delegation, provide client credentials
        auth_provider = AuthProvider(
            zone_id="abc1234",
            mcp_server_name="My FastMCP Service",
            mcp_base_url="http://localhost:8000/",
            auth=BasicAuth("client_id", "client_secret")
        )

        # Get the RemoteAuthProvider for FastMCP
        auth = auth_provider.get_remote_auth_provider()
        mcp = FastMCP("My Protected Service", auth=auth)

        # Use grant decorator for token exchange
        @mcp.tool()
        @auth_provider.grant("https://api.example.com")
        def my_tool(ctx: Context, user_id: str):
            # Use access context to check the status of the token exchange
            # and handle the error state accordingly
            access_context: AccessContext = ctx.get_state("keycardai")
            if access_context.has_errors():
                print("Failed to obtain access token for resource")
                print(f"Error: {access_context.get_errors()}")
                return
            token = access_context.access("https://api.example.com").access_token
            # Use token to call external API
            return f"Data for user {user_id}"
        ```

    Advanced use cases:
    - If you want to customize the HTTP clients used in the discovery or token exchange, you can provide a custom client factory.

    """

    def __init__(
        self,
        *,
        zone_id: str | None = None,
        zone_url: str | None = None,
        mcp_server_name: str | None = None,
        required_scopes: list[str] | None = None,
        mcp_base_url: str,
        base_url: str | None = None,
        auth: AuthStrategy | None = None,
        client_factory: ClientFactory | None = None,
    ):
        """Initialize Keycard authentication provider.

        Args:
            zone_id: Keycard zone ID for OAuth operations.
            zone_url: Keycard zone URL for OAuth operations. If not provided and zone_id is given,
                     will be constructed using base_url or default keycard.cloud domain.
            mcp_server_name: Human-readable service name for metadata
            required_scopes: Required Keycard scopes for access
            mcp_base_url: Resource server URL for the FastMCP server
            base_url: Base URL for Keycard tenant
            auth: Authentication strategy for OAuth operations. Defaults to NoneAuth() for
                 automatic client registration. For client credentials, use BasicAuth
            client_factory: Client factory for creating OAuth clients. Defaults to DefaultClientFactory
        """
        if zone_url is None and zone_id is None:
            raise AuthProviderConfigurationError()

        self.zone_url = self._build_zone_url(zone_url, zone_id, base_url)
        self.mcp_server_name = mcp_server_name or "Authenticated FastMCP Server"
        self.required_scopes = required_scopes or []
        # Appends `/` to any URL. Required to ensure audience is properly aligned with FastMCP JWTVerifier which appends `/` to the audience.
        self.mcp_base_url = str(AnyHttpUrl(mcp_base_url))
        self.client_name = self.mcp_server_name or "Keycard Auth Client"

        self.client_factory = client_factory or DefaultClientFactory()

        # OAuth client for token exchange operations
        # FastMCP operates in the async context, so we need to use an async client
        self.client: AsyncClient | None = self.client_factory.create_async_client(self.zone_url, auth=auth)
        if self.client is None:
            raise OAuthClientConfigurationError()

        try:
            self.jwks_uri = self._discover_jwks_uri(self.client_factory.create_client(self.zone_url))
        except Exception as e:
            raise MetadataDiscoveryError() from e

        self.auth = auth if auth is not None else NoneAuth()

    def _build_zone_url(self, zone_url: str | None, zone_id: str | None, base_url: str | None) -> str:
        """Build the zone URL from the provided parameters.

        Args:
            zone_url: Explicit zone URL if provided
            zone_id: Zone ID to construct URL from
            base_url: Custom base URL for zone construction

        Returns:
            str: The constructed zone URL
        """
        if zone_url is not None:
            return zone_url

        if base_url:
            base_url_obj = AnyHttpUrl(base_url)
            # Only include port if it's non-default (not 443 for https, not 80 for http)
            default_ports = {"https": 443, "http": 80}
            if base_url_obj.port and base_url_obj.port != default_ports.get(base_url_obj.scheme):
                host_with_port = f"{base_url_obj.host}:{base_url_obj.port}"
            else:
                host_with_port = base_url_obj.host
            constructed_url = f"{base_url_obj.scheme}://{zone_id}.{host_with_port}"
        else:
            constructed_url = f"https://{zone_id}.keycard.cloud"

        return constructed_url

    def _discover_jwks_uri(self, client: Client) -> str:
        """Discover JWKS URI from the OAuth server metadata.

        Args:
            client: OAuth client to use for discovery

        Returns:
            str: The JWKS URI from the server metadata

        Raises:
            Exception: If discovery fails or JWKS URI is not available
        """
        metadata = client.discover_server_metadata()
        if not metadata.jwks_uri:
            raise Exception("Keycard zone does not provide a JWKS URI")
        return metadata.jwks_uri

    def get_jwt_token_verifier(self) -> JWTVerifier:
        """Create a JWT token verifier for Keycard zone tokens.

        Discovers Keycard zone metadata and creates a JWTVerifier configured
        with the zone's JWKS URI and issuer information.

        This method uses eager discovery of the zone metadata, and performs HTTP calls using the initialized client.

        Returns:
            JWTVerifier: Configured JWT token verifier for the Keycard zone

        Raises:
            MetadataDiscoveryError: If zone metadata discovery fails
            JWKSValidationError: If JWKS URI is not available
        """
        return JWTVerifier(
            jwks_uri=self.jwks_uri,
            issuer=self.zone_url,
            required_scopes=self.required_scopes,
            audience=self.mcp_base_url,
        )

    def get_remote_auth_provider(self) -> RemoteAuthProvider:
        """Get a RemoteAuthProvider instance configured for Keycard authentication.

        This method uses eager discovery of the zone metadata, and performs HTTP calls using the initialized client.

        Returns:
            RemoteAuthProvider: Configured authentication provider for use with FastMCP

        Raises:
            ValueError: If zone metadata discovery fails or JWKS URI is not available
        """

        authorization_servers = [AnyHttpUrl(self.zone_url)]

        return RemoteAuthProvider(
            token_verifier=self.get_jwt_token_verifier(),
            authorization_servers=authorization_servers,
            resource_server_url=self.mcp_base_url,
            resource_name=self.mcp_server_name,
        )

    def grant(self, resources: str | list[str]):
        """Decorator for automatic delegated token exchange.

        This decorator automates the OAuth token exchange process for accessing
        external resources on behalf of authenticated users. It follows the FastMCP
        Context namespace pattern, making tokens available through ctx.get_state("keycardai").

        The returned value is an instance of AccessContext, which can be used to check the status of the token exchange

        The decorator avoids raising exceptions, and instead sets the error state in the AccessContext.

        Args:
            resources: Target resource URL(s) for token exchange.
                      Can be a single string or list of strings.
                      (e.g., "https://api.example.com" or
                       ["https://api.example.com", "https://other-api.com"])

        Usage:
            ```python
            from fastmcp import FastMCP, Context
            from keycardai.mcp.integrations.fastmcp import AuthProvider, AccessContext

            auth_provider = AuthProvider(zone_id="abc1234", mcp_base_url="http://localhost:8000")
            auth = auth_provider.get_remote_auth_provider()
            mcp = FastMCP("Server", auth=auth)

            @mcp.tool()
            @auth_provider.grant("https://api.example.com")
            def my_tool(ctx: Context, user_id: str):
                # Access token available through context namespace
                access_context: AccessContext = ctx.get_state("keycardai")
                if access_context.has_errors():
                    print("Failed to obtain access token for resource")
                    print(f"Error: {access_context.get_errors()}")
                    return
                token = access_context.access("https://api.example.com").access_token
                headers = {"Authorization": f"Bearer {token}"}
                # Use headers to call external API
                return f"Data for {user_id}"

            # Also works with async functions
            @mcp.tool()
            @auth_provider.grant("https://api.example.com")
            async def my_async_tool(ctx: Context, user_id: str):
                token = ctx.get_state("keycardai").access("https://api.example.com").access_token
                # Async API call
                return f"Async data for {user_id}"
            ```

        The decorated function must:
        - Have a Context parameter from FastMCP (e.g., `ctx: Context`)
        - Can be either async or sync (the decorator handles both cases automatically)

        Error handling:
        - Returns structured error response if token exchange fails
        - Preserves original function signature and behavior
        - Provides detailed error messages for debugging
        """
        def _has_context(func: Callable) -> bool:
            sig = inspect.signature(func)
            for value in sig.parameters.values():
                if value.annotation == Context:
                    return True
            return False

        def _get_context(*args, **kwargs) -> Context | None:
            for value in args:
                if isinstance(value, Context):
                    return value
            for value in kwargs.values():
                if isinstance(value, Context):
                    return value
            return None

        def _set_error(error: dict[str, str], resource: str | None, access_context: AccessContext, ctx: Context):
            """Helper to set error context and call function."""
            if resource:
                access_context.set_resource_error(resource, error)
            else:
                access_context.set_error(error)
            ctx.set_state("keycardai", access_context)

        async def _call_func(is_async_func: bool, func: Callable, *args, **kwargs):
            if is_async_func:
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)

        def decorator(func: Callable) -> Callable:
            is_async_func = inspect.iscoroutinefunction(func)
            if not _has_context(func):
                raise MissingContextError()

            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                _ctx = _get_context(*args, **kwargs)
                if _ctx is None:
                    raise MissingContextError()

                _access_context = AccessContext()
                try:
                    _user_token = get_access_token()
                    if not _user_token:
                        _set_error({
                            "error": "No authentication token available. Please ensure you're properly authenticated.",
                        }, None, _access_context, _ctx)
                        return await _call_func(is_async_func, func, *args, **kwargs)
                except Exception as e:
                    _set_error({
                        "error": "Failed to get access token from context. Ensure the Context parameter is properly annotated.",
                        "raw_error": str(e),
                    }, None, _access_context, _ctx)
                    return await _call_func(is_async_func, func, *args, **kwargs)
                _resource_list = [resources] if isinstance(resources, str) else resources
                _access_tokens = {}
                for resource in _resource_list:
                    try:
                        _token_response = await self.client.exchange_token(
                            subject_token=_user_token.token,
                            resource=resource,
                            subject_token_type="urn:ietf:params:oauth:token-type:access_token"
                        )
                        _access_tokens[resource] = _token_response
                    except Exception as e:
                        _set_error({
                            "error": f"Token exchange failed for {resource}: {e}",
                            "raw_error": str(e),
                        }, resource, _access_context, _ctx)
                        return await _call_func(is_async_func, func, *args, **kwargs)

                # Set successful tokens on the existing access_context (preserves any resource errors)
                _access_context.set_bulk_tokens(_access_tokens)
                _ctx.set_state("keycardai", _access_context)
                return await _call_func(is_async_func, func, *args, **kwargs)
            return wrapper
        return decorator
