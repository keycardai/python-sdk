"""KeyCard authentication provider for FastMCP.

This module provides AuthProvider, which integrates KeyCard's OAuth
token verification with FastMCP's authentication system. The AuthProvider
creates a RemoteAuthProvider instance with automatic KeyCard zone discovery
and JWT token verification.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings

from fastmcp import Context
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token
from keycardai.oauth import AsyncClient, Client, ClientConfig
from keycardai.oauth.http.auth import AuthStrategy, NoneAuth
from keycardai.oauth.types.models import TokenResponse


class AccessContext:
    """Context object that provides access to exchanged tokens for specific resources."""

    def __init__(self, access_tokens: dict[str, TokenResponse]):
        """Initialize with access tokens for resources.

        Args:
            access_tokens: Dict mapping resource URLs to their TokenResponse objects
        """
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
            raise KeyError(
                f"Resource '{resource}' not granted. Available resources: {list(self._access_tokens.keys())}"
            )
        return self._access_tokens[resource]


class AuthProviderSettings(BaseSettings):
    """Settings for KeyCard authentication provider."""

    zone_id: str | None = None
    zone_url: str | None = None
    mcp_server_name: str | None = None
    required_scopes: list[str] | None = None
    mcp_server_url: AnyHttpUrl | str | None = None
    base_url: str | None = None

    @field_validator("required_scopes", mode="before")
    @classmethod
    def _parse_scopes(cls, v):
        if isinstance(v, str):
            return [scope.strip() for scope in v.split(",") if scope.strip()]
        return v


class AuthProvider:
    """KeyCard authentication provider for FastMCP.

    This provider integrates KeyCard's zone-based authentication with FastMCP's
    authentication system. It provides a clean interface for configuring KeyCard
    authentication and returns a RemoteAuthProvider instance for FastMCP integration.

    Features:
    - Automatic KeyCard zone metadata discovery
    - JWT token verification with JWKS endpoint discovery
    - Configurable OAuth scope requirements
    - RemoteAuthProvider creation for FastMCP integration
    - Support for both zone_id and zone_url configuration
    - Token exchange with grant decorator for delegated access
    - Thread-safe OAuth client initialization
    - Flexible authentication strategies (NoneAuth, BasicAuth, MultiZoneBasicAuth)

    Example:
        ```python
        from fastmcp import FastMCP, Context
        from keycardai.mcp.integrations.fastmcp import AuthProvider

        # Using zone_id (recommended)
        auth_provider = AuthProvider(
            zone_id="abc1234",
            mcp_server_name="My FastMCP Service",
            required_scopes=["calendar:read", "drive:read"],
            mcp_server_url="http://localhost:8000"
        )

        # Or using full zone_url
        auth_provider = AuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My FastMCP Service",
            mcp_server_url="http://localhost:8000"
        )

        # With custom authentication (e.g., BasicAuth for client credentials)
        from keycardai.mcp.integrations.fastmcp import BasicAuth

        auth_provider = AuthProvider(
            zone_id="abc1234",
            mcp_server_name="My FastMCP Service",
            mcp_server_url="http://localhost:8000",
            auth=BasicAuth("client_id", "client_secret")
        )

        # Get the RemoteAuthProvider for FastMCP
        auth = auth_provider.get_remote_auth_provider()
        mcp = FastMCP("My Protected Service", auth=auth)

        # Use grant decorator for token exchange
        @mcp.tool()
        @auth_provider.grant("https://api.example.com")
        def my_tool(ctx: Context, user_id: str):
            token = ctx.get_state("keycardai").access("https://api.example.com").access_token
            # Use token to call external API
            return f"Data for user {user_id}"
        ```
    """

    def __init__(
        self,
        *,
        zone_id: str | None = None,
        zone_url: str | None = None,
        mcp_server_name: str | None = None,
        required_scopes: list[str] | None = None,
        mcp_server_url: AnyHttpUrl | str,
        base_url: str | None = None,
        auth: AuthStrategy | None = None,
    ):
        """Initialize KeyCard authentication provider.

        Args:
            zone_id: KeyCard zone ID for OAuth operations.
            zone_url: KeyCard zone URL for OAuth operations. If not provided and zone_id is given,
                     will be constructed using base_url or default keycard.cloud domain.
            mcp_server_name: Human-readable service name for metadata
            required_scopes: Required KeyCard scopes for access
            mcp_server_url: Resource server URL for the FastMCP server
            base_url: Base URL for constructing zone URLs from zone_id
            auth: Authentication strategy for OAuth operations. Defaults to NoneAuth() for
                 automatic client registration. For multi-zone scenarios, use MultiZoneBasicAuth
        """
        settings = AuthProviderSettings.model_validate({
            "zone_id": zone_id,
            "zone_url": zone_url,
            "mcp_server_name": mcp_server_name,
            "required_scopes": required_scopes,
            "mcp_server_url": mcp_server_url,
            "base_url": base_url,
        })

        if settings.zone_url is None and settings.zone_id is None:
            raise ValueError("zone_url or zone_id is required")

        if settings.zone_url is None:
            if settings.base_url:
                base_url_obj = AnyHttpUrl(settings.base_url)
                settings.zone_url = f"{base_url_obj.scheme}://{settings.zone_id}.{base_url_obj.host}"
            else:
                settings.zone_url = f"https://{settings.zone_id}.keycard.cloud"

        self.zone_url = settings.zone_url.rstrip("/")
        self.mcp_server_name = settings.mcp_server_name or "FastMCP Service with KeyCard Auth"
        self.required_scopes = settings.required_scopes or []
        self.mcp_server_url = settings.mcp_server_url
        self.client_name = self.mcp_server_name or "FastMCP OAuth Client"

        # OAuth client for token exchange operations
        self._client: AsyncClient | None = None
        self._init_lock: asyncio.Lock | None = None
        self.auth = auth if auth is not None else NoneAuth()
        if isinstance(self.auth, NoneAuth):
            self.auto_register_client = True
        else:
            self.auto_register_client = False

    def get_jwt_token_verifier(self) -> JWTVerifier:
        """Create a JWT token verifier for KeyCard zone tokens.

        Discovers KeyCard zone metadata and creates a JWTVerifier configured
        with the zone's JWKS URI and issuer information.

        Returns:
            JWTVerifier: Configured JWT token verifier for the KeyCard zone

        Raises:
            ValueError: If zone metadata discovery fails or JWKS URI is not available
        """
        try:
            client_config = ClientConfig(
                enable_metadata_discovery=True,
                auto_register_client=False,
            )

            with Client(
                base_url=self.zone_url,
                config=client_config,
            ) as client:
                metadata = client.discover_server_metadata()

                jwks_uri = metadata.jwks_uri
                issuer = metadata.issuer

                if not jwks_uri:
                    raise ValueError(f"KeyCard zone {self.zone_url} does not provide JWKS URI")

        except Exception as e:
            raise ValueError(f"Failed to discover KeyCard zone endpoints: {e}") from e

        return JWTVerifier(
            jwks_uri=jwks_uri,
            issuer=issuer,
            required_scopes=self.required_scopes,
            audience=self.mcp_server_url,
        )

    def get_remote_auth_provider(self) -> RemoteAuthProvider:
        """Get a RemoteAuthProvider instance configured for KeyCard authentication.

        Returns:
            RemoteAuthProvider: Configured authentication provider for use with FastMCP
        """

        authorization_servers = [AnyHttpUrl(self.zone_url)]

        return RemoteAuthProvider(
            token_verifier=self.get_jwt_token_verifier(),
            authorization_servers=authorization_servers,
            resource_server_url=self.mcp_server_url,
            resource_name=self.mcp_server_name,
        )

    async def _ensure_client_initialized(self):
        """Initialize OAuth client if not already done.

        This method provides thread-safe initialization of the OAuth client
        for token exchange operations.
        """
        if self._client is not None:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if self._client is not None:
                return

            try:
                client_config = ClientConfig(
                    client_name=self.client_name,
                    auto_register_client=self.auto_register_client,
                    enable_metadata_discovery=True,
                )

                self._client = AsyncClient(
                    base_url=self.zone_url,
                    config=client_config,
                    auth=self.auth,
                )

            except Exception:
                self._client = None
                raise

    def grant(self, resources: str | list[str]):
        """Decorator for automatic delegated token exchange.

        This decorator automates the OAuth token exchange process for accessing
        external resources on behalf of authenticated users. It follows the FastMCP
        Context namespace pattern, making tokens available through ctx.get_state("keycardai").

        Args:
            resources: Target resource URL(s) for token exchange.
                      Can be a single string or list of strings.
                      (e.g., "https://api.example.com" or
                       ["https://api.example.com", "https://other-api.com"])

        Usage:
            ```python
            from fastmcp import FastMCP, Context
            from keycardai.mcp.integrations.fastmcp import AuthProvider

            auth_provider = AuthProvider(zone_id="abc1234", mcp_server_url="http://localhost:8000")
            auth = auth_provider.get_remote_auth_provider()
            mcp = FastMCP("Server", auth=auth)

            @mcp.tool()
            @auth_provider.grant("https://api.example.com")
            def my_tool(ctx: Context, user_id: str):
                # Access token available through context namespace
                token = ctx.get_state("keycardai").access("https://api.example.com").access_token
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
        def decorator(func: Callable) -> Callable:
            # Check if function is async - FastMCP always runs in async mode but tools can be sync
            is_async_func = inspect.iscoroutinefunction(func)

            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                try:
                    # Find Context parameter in function arguments
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

                    # Get user token from FastMCP auth context
                    user_token = get_access_token()
                    if not user_token:
                        return {
                            "error": "No authentication token available. Please ensure you're properly authenticated.",
                            "isError": True,
                            "errorType": "authentication_required",
                        }

                    await self._ensure_client_initialized()

                    if self._client is None:
                        return {
                            "error": "OAuth client not available. Server configuration issue.",
                            "isError": True,
                            "errorType": "server_configuration",
                        }

                    resource_list = [resources] if isinstance(resources, str) else resources

                    access_tokens = {}
                    for resource in resource_list:
                        try:
                            token_response = await self._client.exchange_token(
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

                    # Set keycardai namespace in context
                    access_context = AccessContext(access_tokens)
                    ctx.set_state("keycardai", access_context)

                    # Call the original function - handle both sync and async
                    if is_async_func:
                        return await func(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)

                except Exception as e:
                    print(f"Unexpected error in delegated token exchange: {e}")
                    return {
                        "error": f"Unexpected error in delegated token exchange: {e}",
                        "isError": True,
                        "errorType": "unexpected_error",
                        "resources": resource_list if 'resource_list' in locals() else resources,
                    }

            return wrapper
        return decorator
