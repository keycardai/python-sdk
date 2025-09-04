"""OAuth 2.0 client implementation following sync/async API design standard.

This module provides separate AsyncClient and Client classes with proper I/O separation,
sharing a sync-agnostic core as defined in the sync/async API design standard.
"""

import asyncio
import threading
from typing import Any, overload

from .exceptions import (
    AuthenticationError,
    ConfigError,
    NetworkError,
    OAuthHttpError,
    OAuthProtocolError,
)
from .http._context import HTTPContext
from .http._transports import HttpxAsyncTransport, HttpxTransport
from .http.auth import (
    AuthStrategy,
    NoneAuth,
)
from .http.transport import AsyncHTTPTransport, HTTPTransport
from .operations._discovery import (
    discover_server_metadata,
    discover_server_metadata_async,
)
from .operations._registration import (
    _build_client_registration_request_from_kwargs,
    register_client,
    register_client_async,
)
from .operations._token_exchange import token_exchange, token_exchange_async
from .types.models import (
    AuthorizationServerMetadata,
    ClientConfig,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    Endpoints,
    ServerMetadataRequest,
    TokenExchangeRequest,
    TokenResponse,
)
from .types.oauth import (
    GrantType,
    OAuth2DefaultEndpoints,
    ResponseType,
    TokenEndpointAuthMethod,
)
from .utils.jwt import extract_jwt_client_id


def resolve_endpoints(
    base_url: str,
    endpoint_overrides: Endpoints | None = None,
    discovered_metadata: "AuthorizationServerMetadata | None" = None,
) -> Endpoints:
    """Resolve final endpoint URLs with priority: overrides > discovered > defaults.

    Args:
        base_url: Base URL for OAuth 2.0 server
        endpoint_overrides: Optional endpoint overrides (highest priority)
        discovered_metadata: Optional discovered server metadata (middle priority)

    Returns:
        Resolved endpoints configuration with proper priority handling
    """
    endpoints = Endpoints()

    if endpoint_overrides:
        endpoints.token = endpoint_overrides.token
        endpoints.introspect = endpoint_overrides.introspect
        endpoints.revoke = endpoint_overrides.revoke
        endpoints.register = endpoint_overrides.register
        endpoints.par = endpoint_overrides.par
        endpoints.authorize = endpoint_overrides.authorize

    if discovered_metadata:
        if not endpoints.token:
            endpoints.token = discovered_metadata.token_endpoint
        if not endpoints.introspect:
            endpoints.introspect = discovered_metadata.introspection_endpoint
        if not endpoints.revoke:
            endpoints.revoke = discovered_metadata.revocation_endpoint
        if not endpoints.register:
            endpoints.register = discovered_metadata.registration_endpoint
        if not endpoints.par:
            endpoints.par = discovered_metadata.pushed_authorization_request_endpoint
        if not endpoints.authorize:
            endpoints.authorize = discovered_metadata.authorization_endpoint

    if not endpoints.introspect:
        endpoints.introspect = OAuth2DefaultEndpoints.construct_url(base_url, OAuth2DefaultEndpoints.INTROSPECTION)
    if not endpoints.token:
        endpoints.token = OAuth2DefaultEndpoints.construct_url(base_url, OAuth2DefaultEndpoints.TOKEN)
    if not endpoints.revoke:
        endpoints.revoke = OAuth2DefaultEndpoints.construct_url(base_url, OAuth2DefaultEndpoints.REVOCATION)
    if not endpoints.register:
        endpoints.register = OAuth2DefaultEndpoints.construct_url(base_url, OAuth2DefaultEndpoints.REGISTRATION)
    if not endpoints.authorize:
        endpoints.authorize = OAuth2DefaultEndpoints.construct_url(base_url, OAuth2DefaultEndpoints.AUTHORIZATION)
    if not endpoints.par:
        endpoints.par = OAuth2DefaultEndpoints.construct_url(base_url, OAuth2DefaultEndpoints.PUSHED_AUTHORIZATION)

    return endpoints


def create_endpoints_summary(
    endpoints: Endpoints,
    base_url: str,
    endpoint_overrides: Endpoints | None = None,
    discovered_metadata: "AuthorizationServerMetadata | None" = None,
) -> dict[str, dict[str, str]]:
    """Create diagnostic summary of resolved endpoints showing their sources.

    Args:
        endpoints: Resolved endpoints configuration
        base_url: Base URL for OAuth 2.0 server
        endpoint_overrides: Optional endpoint overrides
        discovered_metadata: Optional discovered server metadata

    Returns:
        Dictionary showing resolved URLs and their sources (override/discovered/default)
    """
    def determine_source(endpoint_name: str, url: str | None) -> str:
        if not url:
            return "none"

        if endpoint_overrides:
            override_value = getattr(endpoint_overrides, endpoint_name, None)
            if override_value and override_value == url:
                return "override"

        if discovered_metadata:
            metadata_field_map = {
                "token": "token_endpoint",
                "introspect": "introspection_endpoint",
                "revoke": "revocation_endpoint",
                "register": "registration_endpoint",
                "par": "pushed_authorization_request_endpoint",
                "authorize": "authorization_endpoint",
            }
            if endpoint_name in metadata_field_map:
                metadata_value = getattr(discovered_metadata, metadata_field_map[endpoint_name], None)
                if metadata_value and metadata_value == url:
                    return "discovered"

        return "default"

    return {
        "introspect": {
            "url": endpoints.introspect or "",
            "source": determine_source("introspect", endpoints.introspect),
        },
        "token": {
            "url": endpoints.token or "",
            "source": determine_source("token", endpoints.token),
        },
        "revoke": {
            "url": endpoints.revoke or "",
            "source": determine_source("revoke", endpoints.revoke),
        },
        "register": {
            "url": endpoints.register or "",
            "source": determine_source("register", endpoints.register),
        },
        "authorize": {
            "url": endpoints.authorize or "",
            "source": determine_source("authorize", endpoints.authorize),
        },
        "par": {
            "url": endpoints.par or "",
            "source": determine_source("par", endpoints.par),
        },
    }


class AsyncClient:
    """Asynchronous OAuth 2.0 client for async/await environments.

    Must be used inside an event loop (asyncio). Provides native async I/O
    operations for optimal performance in async applications.

    This client implements the async context manager protocol and should be used
    with 'async with' statements to ensure proper initialization and cleanup.

    Automatically performs server metadata discovery (RFC 8414) and client
    registration during context entry unless explicitly disabled via ClientConfig.

    Concurrency Safety:
        This client is safe for concurrent async operations. Initialization
        (client registration and endpoint discovery) is performed once during
        context entry and protected by internal async locking.

    Example:
        # Recommended usage with context manager
        async with AsyncClient(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("my_client_id", "my_client_secret")
        ) as client:
            client_id = await client.get_client_id()
            response = await client.exchange_token(request)

        # Enterprise usage with custom configuration
        async with AsyncClient(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("enterprise_client", "enterprise_secret"),
            endpoints=Endpoints(
                register="https://register.internal.com/oauth2/register"
            ),
            config=ClientConfig(timeout=60, max_retries=5)
        ) as client:
            # Client is fully initialized here
            tokens = await client.exchange_token(request)

        # Disable automatic discovery and registration
        async with AsyncClient(
            "https://api.keycard.ai",
            auth=NoneAuth(),
            config=ClientConfig(
                enable_metadata_discovery=False,
                auto_register_client=False
            )
        ) as client:
            # Manual operations only
            metadata = await client.discover_server_metadata()
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: AuthStrategy | None = None,
        endpoints: Endpoints | None = None,
        transport: AsyncHTTPTransport | None = None,
        config: ClientConfig | None = None,
    ):
        """Initialize asynchronous OAuth 2.0 client.

        Args:
            base_url: Base URL for OAuth 2.0 server
            auth: Authentication strategy (ClientCredentialsAuth, JWTAuth, MTLSAuth, NoneAuth)
            endpoints: Endpoint overrides for multi-server deployments
            transport: Custom asynchronous HTTP transport
            config: Client configuration with timeouts, retries, etc.
        """
        if not base_url:
            raise ConfigError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self.config = config or ClientConfig()

        self.auth_strategy = auth or NoneAuth()

        if transport is not None:
            self.transport = transport
            self._owns_transport = False
        else:
            self.transport = HttpxAsyncTransport(
                config=self.config,
            )
            self._owns_transport = True

        self._endpoint_overrides = endpoints

        # Initialize with basic endpoints, discovery happens lazily
        self._endpoints = resolve_endpoints(self.base_url, endpoints)

        # Lazy initialization state (async-safe)
        self._initialized = False
        self._init_lock: asyncio.Lock | None = None

        # Will be set during lazy initialization
        self._client_id = None
        self._client_secret = None
        self._discovered_endpoints: Endpoints | None = None

    async def _ensure_initialized(self) -> None:
        """Ensure client is fully initialized with discovery and registration.

        This method performs lazy initialization in an async-safe manner:
        1. Endpoint discovery (if enabled)
        2. Client registration (if auto_register_client is True)

        Uses async lock for concurrent operation safety.
        """
        if self._initialized:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            # Double-check: another coroutine might have initialized while we waited
            if self._initialized:
                return

            if self.config.enable_metadata_discovery:
                try:
                    metadata = await self.discover_server_metadata()
                    self._discovered_endpoints = resolve_endpoints(
                        self.base_url,
                        self._endpoint_overrides,
                        metadata
                    )
                except (OAuthHttpError, OAuthProtocolError, NetworkError, AuthenticationError):
                    self._discovered_endpoints = resolve_endpoints(
                        self.base_url,
                        self._endpoint_overrides,
                        None
                    )
            else:
                self._discovered_endpoints = self._endpoints

            if self.config.auto_register_client:
                ctx = HTTPContext(
                    endpoint=self._discovered_endpoints.register,
                    transport=self.transport,
                    auth=self.auth_strategy,
                    timeout=self.config.timeout,
                )
                client_registration_response = await register_client_async(
                    ClientRegistrationRequest(
                        client_name=self.config.client_name,
                        redirect_uris=self.config.client_redirect_uris,
                        grant_types=self.config.client_grant_types,
                        token_endpoint_auth_method=self.config.client_token_endpoint_auth_method,
                    ),
                    ctx
                )
                self._client_id = client_registration_response.client_id
                self._client_secret = client_registration_response.client_secret

            self._initialized = True

    async def __aenter__(self) -> "AsyncClient":
        """Enter async context manager.

        Performs full client initialization including:
        - Server metadata discovery (if enabled)
        - Dynamic client registration (if enabled)

        Returns:
            Fully initialized AsyncClient instance
        """
        await self._ensure_initialized()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        """Exit async context manager.

        Performs cleanup of resources if the client owns them.

        Args:
            exc_type: Exception type (if any)
            exc_value: Exception value (if any)
            traceback: Exception traceback (if any)
        """
        pass

    async def get_client_id(self) -> str | None:
        """Get the client ID obtained from registration.

        Returns:
            Client ID string if registration was successful, None otherwise

        Raises:
            RuntimeError: If called outside of async context manager
        """
        if not self._initialized:
            raise RuntimeError(
                "AsyncClient must be used within 'async with' statement. "
                "Use 'async with AsyncClient(...) as client:' to properly initialize."
            )
        return self._client_id

    async def get_client_secret(self) -> str | None:
        """Get the client secret obtained from registration.

        Returns:
            Client secret string if registration was successful, None otherwise

        Raises:
            RuntimeError: If called outside of async context manager
        """
        if not self._initialized:
            raise RuntimeError(
                "AsyncClient must be used within 'async with' statement. "
                "Use 'async with AsyncClient(...) as client:' to properly initialize."
            )
        return self._client_secret

    async def _get_current_endpoints(self) -> "Endpoints":
        """Get current endpoints from cached discovery.

        Returns endpoints resolved during initialization. This avoids repeating
        discovery on every operation and ensures consistent endpoint usage.
        """
        await self._ensure_initialized()
        return self._discovered_endpoints or self._endpoints

    @overload
    async def register_client(
        self,
        request: ClientRegistrationRequest,
    ) -> ClientRegistrationResponse: ...

    @overload
    async def register_client(
        self,
        /,
        *,
        client_name: str,
        redirect_uris: list[str] | None = None,
        jwks_uri: str | None = None,
        jwks: dict | None = None,
        scope: str | None = None,
        grant_types: list[GrantType] | None = None,
        response_types: list[ResponseType] | None = None,
        token_endpoint_auth_method: TokenEndpointAuthMethod | None = None,
        additional_metadata: dict[str, Any] | None = None,
        client_uri: str | None = None,
        logo_uri: str | None = None,
        tos_uri: str | None = None,
        policy_uri: str | None = None,
        software_id: str | None = None,
        software_version: str | None = None,
        timeout: float | None = None,
    ) -> ClientRegistrationResponse: ...

    async def register_client(self, request: ClientRegistrationRequest | None = None, /, **client_registration_args) -> ClientRegistrationResponse:
        """Register a new OAuth 2.0 client with the authorization server.

        Either pass a fully-formed ClientRegistrationRequest *or* call with keyword
        args for common cases.

        Simple usage:
            request = ClientRegistrationRequest(
                client_name="MyService",
            )
            response = await client.register_client(request)

        Or with keyword arguments:
            response = await client.register_client(
                client_name="MyService",
                jwks_uri="https://zone1234.keycard.cloud/.well-known/jwks.json"
            )

        Full control:
            request = ClientRegistrationRequest(
                client_name="WebApp",
                redirect_uris=["https://app.com/callback"],
                grant_types=["authorization_code", "refresh_token"],
                scope="openid profile email",
                token_endpoint_auth_method="private_key_jwt",
                additional_metadata={"policy_uri": "https://app.com/privacy"}
            )
            response = await client.register_client(request)

        Args:
            request: ClientRegistrationRequest with all registration parameters
            **client_registration_args: Alternative to request - provide individual parameters

        Returns:
            ClientRegistrationResponse with client credentials and metadata

        Raises:
            TypeError: If both request and client_registration_args are provided
        """
        if request is not None and client_registration_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = _build_client_registration_request_from_kwargs(**client_registration_args)

        endpoints = await self._get_current_endpoints()

        ctx = HTTPContext(
            endpoint=endpoints.register,
            transport=self.transport,
            auth=self.auth_strategy,
            timeout=client_registration_args.get("timeout", self.config.timeout),
        )

        return await register_client_async(request, ctx)

    async def discover_server_metadata(
        self,
        request: ServerMetadataRequest | None = None,
    ) -> AuthorizationServerMetadata:
        """Discover OAuth 2.0 authorization server metadata.

        Simple usage:
            metadata = await client.discover_server_metadata_async()
            print(f"Token endpoint: {metadata.token_endpoint}")

        Explicit request:
            request = ServerMetadataRequest(base_url="https://auth.example.com")
            metadata = await client.discover_server_metadata_async(request)

        Args:
            request: Optional ServerMetadataRequest (defaults to client's base_url if not provided)

        Returns:
            AuthorizationServerMetadata with discovered server capabilities

        Raises:
            ConfigError: If base_url is empty
            OAuthHttpError: If discovery endpoint is unreachable
            OAuthProtocolError: If metadata format is invalid
            NetworkError: If network request fails
        """
        if request is None:
            request = ServerMetadataRequest(base_url=self.base_url)

        context = HTTPContext(
            endpoint=self.base_url,
            transport=self.transport,
            timeout=self.config.timeout,
            auth=self.auth_strategy,
        )

        return await discover_server_metadata_async(
            request=request,
            context=context,
        )

    async def token_exchange(
        self,
        request: TokenExchangeRequest,
    ) -> TokenResponse:
        """Perform OAuth 2.0 Token Exchange.

        Simple usage (delegation):
            request = TokenExchangeRequest(
                subject_token="original_access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com"
            )
            response = await client.token_exchange_async(request)

        Advanced usage (impersonation):
            request = TokenExchangeRequest(
                subject_token="admin_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com",
                actor_token="service_token",
                actor_token_type="urn:ietf:params:oauth:token-type:access_token",
                requested_token_type="urn:ietf:params:oauth:token-type:access_token",
                scope="read write"
            )
            response = await client.token_exchange_async(request)

        Args:
            request: TokenExchangeRequest with all exchange parameters

        Returns:
            TokenResponse with the exchanged token and metadata
        """
        endpoints = await self._get_current_endpoints()

        ctx = HTTPContext(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=self.auth_strategy,
            timeout=self.config.timeout,
        )

        return await token_exchange_async(request, ctx)

    async def get_access_token_for_resource(
        self,
        resource: str,
        subject_token: str,
    ) -> str:
        """Get an access token for a specific resource using OAuth 2.0 Token Exchange.

        Usage:
            async with AsyncClient("https://zoneid.keycard.cloud") as client:
                access_token = await client.get_access_token_for_resource(
                    "https://api.example.com",
                    "user_access_token"
                )

        Args:
            resource: The target resource URL to request access for
            subject_token: The current access token to exchange

        Returns:
            str: The exchanged access token for the specified resource
        """
        request = TokenExchangeRequest(
            subject_token=subject_token,
            subject_token_type="urn:ietf:params:oauth:token-type:access_token",
            resource=resource,
            client_id=extract_jwt_client_id(subject_token),
        )
        response = await self.token_exchange(request)
        return response.access_token

    def endpoints_summary(self) -> dict[str, dict[str, str]]:
        """Get diagnostic summary of resolved endpoints.

        Returns:
            Dictionary showing resolved URLs and their sources
        """
        return create_endpoints_summary(
            self._endpoints,
            self.base_url,
            self._endpoint_overrides,
            None  # No cached metadata in simplified version
        )


class Client:
    """Synchronous OAuth 2.0 client for traditional Python applications.

    Uses blocking I/O (requests) and works in any Python environment without
    requiring asyncio knowledge. Safe to use in Jupyter notebooks, GUIs, and
    web servers with existing event loops.

    Automatically performs server metadata discovery (RFC 8414) during first use
    unless discovery is explicitly disabled via ClientConfig.

    Thread Safety:
        This client is thread-safe for all operations. Multiple threads can safely
        share a single client instance. Initialization (client registration and
        endpoint discovery) is performed lazily and protected by internal locking.

    Example:
        # Simple usage with client credentials and automatic discovery
        with Client(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("my_client_id", "my_client_secret")
        ) as client:
            response = client.introspect_token("token_to_validate")

        # Enterprise usage with custom configuration
        client = Client(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("enterprise_client", "enterprise_secret"),
            endpoints=Endpoints(
                introspect="https://validator.internal.com/oauth2/introspect"
            ),
            config=ClientConfig(timeout=60, max_retries=5)
        )

        # Disable automatic discovery
        client = Client(
            "https://api.keycard.ai",
            auth=NoneAuth(),
            config=ClientConfig(enable_metadata_discovery=False)
        )
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: AuthStrategy | None = None,
        endpoints: Endpoints | None = None,
        transport: HTTPTransport | None = None,
        config: ClientConfig | None = None,
    ):
        """Initialize synchronous OAuth 2.0 client.

        Args:
            base_url: Base URL for OAuth 2.0 server
            auth: Authentication strategy (ClientCredentialsAuth, JWTAuth, MTLSAuth, NoneAuth)
            endpoints: Endpoint overrides for multi-server deployments
            transport: Custom synchronous HTTP transport
            config: Client configuration with timeouts, retries, etc.
        """
        if not base_url:
            raise ConfigError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self.config = config or ClientConfig()

        self.auth_strategy = auth or NoneAuth()

        if transport is not None:
            self.transport = transport
            self._owns_transport = False
        else:
            self.transport = HttpxTransport(
                config=self.config,
            )
            self._owns_transport = True

        self._endpoint_overrides = endpoints

        self._endpoints = resolve_endpoints(self.base_url, endpoints)

        # Lazy initialization state (thread-safe)
        self._initialized = False
        self._init_lock = threading.Lock()

        # Will be set during lazy initialization
        self._client_id = None
        self._client_secret = None
        self._discovered_endpoints: Endpoints | None = None

    def _ensure_initialized(self) -> None:
        """Ensure client is fully initialized with discovery and registration.

        This method performs lazy initialization in a thread-safe manner:
        1. Endpoint discovery (if enabled)
        2. Client registration (if auto_register_client is True)

        Uses double-check locking pattern for performance.
        """
        # Fast path: already initialized
        if self._initialized:
            return

        # Slow path: acquire lock for initialization
        with self._init_lock:
            # Double-check: another thread might have initialized while we waited
            if self._initialized:
                return

            # Perform endpoint discovery first
            if self.config.enable_metadata_discovery:
                try:
                    metadata = self.discover_server_metadata()
                    self._discovered_endpoints = resolve_endpoints(
                        self.base_url,
                        self._endpoint_overrides,
                        metadata
                    )
                except (OAuthHttpError, OAuthProtocolError, NetworkError, AuthenticationError):
                    # Discovery failed, use defaults
                    self._discovered_endpoints = resolve_endpoints(
                        self.base_url,
                        self._endpoint_overrides,
                        None
                    )
            else:
                # Discovery disabled, use current endpoints
                self._discovered_endpoints = self._endpoints

            # Perform client registration if configured
            if self.config.auto_register_client:
                # Use discovered endpoints directly to avoid circular dependency
                ctx = HTTPContext(
                    endpoint=self._discovered_endpoints.register,
                    transport=self.transport,
                    auth=self.auth_strategy,
                    timeout=self.config.timeout,
                )
                client_registration_response = register_client(
                    ClientRegistrationRequest(
                        client_name=self.config.client_name,
                        redirect_uris=self.config.client_redirect_uris,
                        grant_types=self.config.client_grant_types,
                        token_endpoint_auth_method=self.config.client_token_endpoint_auth_method,
                    ),
                    ctx
                )
                self._client_id = client_registration_response.client_id
                self._client_secret = client_registration_response.client_secret

            # Mark as initialized
            self._initialized = True

    def _get_current_endpoints(self) -> "Endpoints":
        """Get current endpoints from cached discovery.

        Returns endpoints resolved during initialization. This avoids repeating
        discovery on every operation and ensures consistent endpoint usage.
        """
        self._ensure_initialized()
        return self._discovered_endpoints or self._endpoints

    @property
    def client_id(self) -> str | None:
        """Client ID obtained from registration (lazily initialized).

        Accessing this property will trigger automatic initialization if needed.

        Returns:
            Client ID string if registration was successful, None otherwise
        """
        self._ensure_initialized()
        return self._client_id

    @property
    def client_secret(self) -> str | None:
        """Client secret obtained from registration (lazily initialized).

        Accessing this property will trigger automatic initialization if needed.

        Returns:
            Client secret string if registration was successful, None otherwise
        """
        self._ensure_initialized()
        return self._client_secret


    @overload
    def register_client(
        self,
        request: ClientRegistrationRequest,
    ) -> ClientRegistrationResponse: ...

    @overload
    def register_client(
        self,
        /,
        *,
        client_name: str,
        redirect_uris: list[str] | None = None,
        jwks_uri: str | None = None,
        jwks: dict | None = None,
        scope: str | None = None,
        grant_types: list[GrantType] | None = None,
        response_types: list[ResponseType] | None = None,
        token_endpoint_auth_method: TokenEndpointAuthMethod | None = None,
        additional_metadata: dict[str, Any] | None = None,
        client_uri: str | None = None,
        logo_uri: str | None = None,
        tos_uri: str | None = None,
        policy_uri: str | None = None,
        software_id: str | None = None,
        software_version: str | None = None,
        timeout: float | None = None,
    ) -> ClientRegistrationResponse: ...

    def register_client(self, request: ClientRegistrationRequest | None = None, /, **client_registration_args) -> ClientRegistrationResponse:
        """Register a new OAuth 2.0 client with the authorization server.

        Either pass a fully-formed ClientRegistrationRequest *or* call with keyword
        args for common cases.

        Simple usage (S2S):
            request = ClientRegistrationRequest(
                client_name="MyService",
                jwks_uri="https://zone1234.keycard.cloud/.well-known/jwks.json"
            )
            response = client.register_client(request)

        Or with keyword arguments:
            response = client.register_client(
                client_name="MyService",
                jwks_uri="https://zone1234.keycard.cloud/.well-known/jwks.json"
            )

        Full control:
            request = ClientRegistrationRequest(
                client_name="WebApp",
                redirect_uris=["https://app.com/callback"],
                grant_types=["authorization_code", "refresh_token"],
                scope="openid profile email",
                token_endpoint_auth_method="private_key_jwt",
                additional_metadata={"policy_uri": "https://app.com/privacy"}
            )
            response = client.register_client(request)

        Args:
            request: ClientRegistrationRequest with all registration parameters
            **client_registration_args: Alternative to request - provide individual parameters

        Returns:
            ClientRegistrationResponse with client credentials and metadata

        Raises:
            TypeError: If both request and client_registration_args are provided
        """
        if request is not None and client_registration_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = _build_client_registration_request_from_kwargs(**client_registration_args)

        endpoints = self._get_current_endpoints()

        ctx = HTTPContext(
            endpoint=endpoints.register,
            transport=self.transport,
            auth=self.auth_strategy,
            timeout=client_registration_args.get("timeout", self.config.timeout),
        )

        return register_client(request, ctx)

    def discover_server_metadata(
        self,
        request: ServerMetadataRequest | None = None,
    ) -> AuthorizationServerMetadata:
        """Discover OAuth 2.0 authorization server metadata.

        Simple usage:
            metadata = client.discover_server_metadata()
            print(f"Token endpoint: {metadata.token_endpoint}")

        Explicit request:
            request = ServerMetadataRequest(base_url="https://auth.example.com")
            metadata = client.discover_server_metadata(request)

        Args:
            request: Optional ServerMetadataRequest (defaults to client's base_url if not provided)
            timeout: Optional timeout override (overrides request.timeout if provided)

        Returns:
            AuthorizationServerMetadata with discovered server capabilities

        Raises:
            ConfigError: If base_url is empty
            OAuthHttpError: If discovery endpoint is unreachable
            OAuthProtocolError: If metadata format is invalid
            NetworkError: If network request fails
        """
        if request is None:
            request = ServerMetadataRequest(base_url=self.base_url)

        context = HTTPContext(
            endpoint=self.base_url,
            transport=self.transport,
            timeout=self.config.timeout,
            auth=self.auth_strategy,
        )

        return discover_server_metadata(
            request=request,
            context=context,
        )

    def token_exchange(
        self,
        request: TokenExchangeRequest,
    ) -> TokenResponse:
        """Perform OAuth 2.0 Token Exchange.

        Simple usage (delegation):
            request = TokenExchangeRequest(
                subject_token="original_access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com"
            )
            response = client.token_exchange(request)

        Advanced usage (impersonation):
            request = TokenExchangeRequest(
                subject_token="admin_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com",
                actor_token="service_token",
                actor_token_type="urn:ietf:params:oauth:token-type:access_token",
                requested_token_type="urn:ietf:params:oauth:token-type:access_token",
                scope="read write"
            )
            response = client.token_exchange(request)

        Args:
            request: TokenExchangeRequest with all exchange parameters

        Returns:
            TokenResponse with the exchanged token and metadata
        """
        endpoints = self._get_current_endpoints()

        ctx = HTTPContext(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=self.auth_strategy,
            timeout=self.config.timeout,
        )

        return token_exchange(request, ctx)

    def endpoints_summary(self) -> dict[str, dict[str, str]]:
        """Get diagnostic summary of resolved endpoints.

        Returns:
            Dictionary showing resolved URLs and their sources
        """
        return create_endpoints_summary(
            self._endpoints,
            self.base_url,
            self._endpoint_overrides,
            None  # No cached metadata in simplified version
        )

    def __enter__(self):
        self._ensure_initialized()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
