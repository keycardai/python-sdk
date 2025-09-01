"""OAuth 2.0 client implementation following sync/async API design standard.

This module provides separate AsyncClient and Client classes with proper I/O separation,
sharing a sync-agnostic core as defined in the sync/async API design standard.
"""

from ..exceptions import ConfigError
from ..types.enums import GrantType, ResponseType, TokenEndpointAuthMethod
from ..types.responses import (
    ClientConfig,
    ClientRegistrationResponse,
    Endpoints,
    IntrospectionResponse,
)
from .auth import (
    AuthStrategy,
)
from .http import AsyncHTTPClient, HTTPClient, HTTPClientProtocol
from .operations import (
    introspect_token,
    introspect_token_async,
    register_client,
    register_client_async,
)


def validate_client_config(
    base_url: str,
    auth_strategy: object,
) -> None:
    """Validate client configuration parameters.

    Args:
        base_url: Base URL for OAuth 2.0 server
        auth_strategy: Authentication strategy

    Raises:
        ConfigError: If configuration is invalid
    """
    if not base_url:
        raise ConfigError("base_url is required")

    if auth_strategy is None:
        raise ConfigError("auth is required")


def resolve_endpoints(base_url: str, endpoint_overrides: Endpoints | None) -> Endpoints:
    """Resolve final endpoint URLs with override priority.

    Args:
        base_url: Base URL for OAuth 2.0 server
        endpoint_overrides: Optional endpoint overrides

    Returns:
        Resolved endpoints configuration
    """
    endpoints = Endpoints()

    if endpoint_overrides:
        endpoints.token = endpoint_overrides.token
        endpoints.introspect = endpoint_overrides.introspect
        endpoints.revoke = endpoint_overrides.revoke
        endpoints.register = endpoint_overrides.register
        endpoints.par = endpoint_overrides.par
        endpoints.authorize = endpoint_overrides.authorize
    if not endpoints.introspect:
        endpoints.introspect = f"{base_url}/oauth2/introspect"
    if not endpoints.token:
        endpoints.token = f"{base_url}/oauth2/token"
    if not endpoints.revoke:
        endpoints.revoke = f"{base_url}/oauth2/revoke"
    if not endpoints.register:
        endpoints.register = f"{base_url}/oauth2/register"
    if not endpoints.authorize:
        endpoints.authorize = f"{base_url}/oauth2/authorize"
    if not endpoints.par:
        endpoints.par = f"{base_url}/oauth2/par"

    return endpoints


def create_endpoints_summary(endpoints: Endpoints) -> dict[str, dict[str, str]]:
    """Create diagnostic summary of resolved endpoints.

    Args:
        endpoints: Resolved endpoints configuration

    Returns:
        Dictionary showing resolved URLs and their sources
    """
    return {
        "introspect": {
            "url": endpoints.introspect or "",
            "source": "configured" if endpoints.introspect else "default",
        },
        "token": {
            "url": endpoints.token or "",
            "source": "configured" if endpoints.token else "default",
        },
        "revoke": {
            "url": endpoints.revoke or "",
            "source": "configured" if endpoints.revoke else "default",
        },
        "register": {
            "url": endpoints.register or "",
            "source": "configured" if endpoints.register else "default",
        },
        "authorize": {
            "url": endpoints.authorize or "",
            "source": "configured" if endpoints.authorize else "default",
        },
        "par": {
            "url": endpoints.par or "",
            "source": "configured" if endpoints.par else "default",
        },
    }


class AsyncClient:
    """Async OAuth 2.0 client for async/await environments.

    Must be used inside an event loop (asyncio). Provides native async I/O
    operations for optimal performance in async applications.

    Example:
        # Simple usage with client credentials
        async with AsyncClient(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("my_client_id", "my_client_secret")
        ) as client:
            response = await client.introspect_token("token_to_validate")

        # Enterprise usage with custom configuration
        client = AsyncClient(
            "https://api.keycard.ai",
            auth=ClientCredentialsAuth("enterprise_client", "enterprise_secret"),
            endpoints=Endpoints(
                introspect="https://validator.internal.com/oauth2/introspect"
            ),
            config=ClientConfig(timeout=60, max_retries=5)
        )

        # Dynamic registration usage (no authentication)
        client = AsyncClient(
            "https://api.keycard.ai",
            auth=NoneAuth()
        )
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: AuthStrategy,
        endpoints: Endpoints | None = None,
        http_client: HTTPClientProtocol | None = None,
        config: ClientConfig | None = None,
    ):
        """Initialize async OAuth 2.0 client.

        Args:
            base_url: Base URL for OAuth 2.0 server
            auth: Authentication strategy (ClientCredentialsAuth, JWTAuth, MTLSAuth, NoneAuth)
            endpoints: Endpoint overrides for multi-server deployments
            http_client: Custom HTTP client for enterprise requirements
            config: Client configuration with timeouts, retries, etc.
        """
        validate_client_config(base_url, auth)

        self.base_url = base_url.rstrip("/")
        self.config = config or ClientConfig()
        self.auth_strategy = auth

        if http_client is not None:
            self.http_client = http_client
        else:
            self.http_client = AsyncHTTPClient(
                timeout=self.config.timeout,
                verify_ssl=self.config.verify_ssl,
                max_retries=self.config.max_retries,
                user_agent=self.config.user_agent,
            )

        self._endpoints = resolve_endpoints(self.base_url, endpoints)

    async def introspect_token(
        self,
        token: str,
        token_type_hint: str | None = None,
        *,
        timeout: float | None = None,
    ) -> IntrospectionResponse:
        """Introspect an OAuth 2.0 token.

        Simple usage:
            response = await client.introspect_token("token_to_check")

        Optimized usage:
            response = await client.introspect_token(
                "token_to_check",
                token_type_hint="access_token"
            )

        Args:
            token: The token to introspect
            token_type_hint: Server optimization hint
            timeout: Optional timeout override

        Returns:
            IntrospectionResponse with token metadata and active status
        """
        return await introspect_token_async(
            token=token,
            introspection_endpoint=self._endpoints.introspect,
            auth_strategy=self.auth_strategy,
            http_client=self.http_client,
            token_type_hint=token_type_hint,
            timeout=timeout,
        )

    async def register_client(
        self,
        *,
        client_name: str,
        jwks_uri: str | None = None,
        jwks: dict | None = None,
        token_endpoint_auth_method: TokenEndpointAuthMethod
        | str = TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
        redirect_uris: list[str] | None = None,
        grant_types: list[GrantType | str] | None = None,
        response_types: list[ResponseType | str] | None = None,
        scope: str | list[str] | None = None,
        timeout: float | None = None,
        **additional_metadata,
    ) -> ClientRegistrationResponse:
        """Register a new OAuth 2.0 client with the authorization server.

        Simple usage (S2S):
            response = await client.register_client(
                client_name="MyService",
                jwks_uri="https://zone1234.keycard.cloud/.well-known/jwks.json"
            )

        Full control:
            response = await client.register_client(
                client_name="WebApp",
                redirect_uris=["https://app.com/callback"],
                grant_types=["authorization_code", "refresh_token"],
                scope=["openid", "profile", "email"],
                token_endpoint_auth_method="private_key_jwt"
            )

        Args:
            client_name: Human-readable client name (required)
            jwks_uri: URL pointing to client's JSON Web Key Set
            jwks: Client's JSON Web Key Set (alternative to jwks_uri)
            token_endpoint_auth_method: Client authentication method
            redirect_uris: Client redirect URIs for authorization code flow
            grant_types: OAuth 2.0 grant types the client will use
            response_types: OAuth 2.0 response types the client will use
            scope: Requested scope for the client (string or list)
            timeout: Optional timeout override
            **additional_metadata: Additional client metadata (vendor extensions)

        Returns:
            ClientRegistrationResponse with client credentials and metadata
        """
        return await register_client_async(
            client_name=client_name,
            registration_endpoint=self._endpoints.register,
            auth_strategy=self.auth_strategy,
            http_client=self.http_client,
            jwks_uri=jwks_uri,
            jwks=jwks,
            token_endpoint_auth_method=token_endpoint_auth_method,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            response_types=response_types,
            scope=scope,
            timeout=timeout,
            **additional_metadata,
        )

    def endpoints_summary(self) -> dict[str, dict[str, str]]:
        """Get diagnostic summary of resolved endpoints.

        Returns:
            Dictionary showing resolved URLs and their sources
        """
        return create_endpoints_summary(self._endpoints)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if hasattr(self.http_client, "aclose"):
            await self.http_client.aclose()


class Client:
    """Synchronous OAuth 2.0 client for traditional Python applications.

    Uses blocking I/O (requests) and works in any Python environment without
    requiring asyncio knowledge. Safe to use in Jupyter notebooks, GUIs, and
    web servers with existing event loops.

    Example:
        # Simple usage with client credentials
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

        # Dynamic registration usage (no authentication)
        client = Client(
            "https://api.keycard.ai",
            auth=NoneAuth()
        )
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth: AuthStrategy,
        endpoints: Endpoints | None = None,
        http_client: HTTPClient | None = None,
        config: ClientConfig | None = None,
    ):
        """Initialize synchronous OAuth 2.0 client.

        Args:
            base_url: Base URL for OAuth 2.0 server
            auth: Authentication strategy (ClientCredentialsAuth, JWTAuth, MTLSAuth, NoneAuth)
            endpoints: Endpoint overrides for multi-server deployments
            http_client: Custom synchronous HTTP client
            config: Client configuration with timeouts, retries, etc.
        """
        validate_client_config(base_url, auth)

        self.base_url = base_url.rstrip("/")
        self.config = config or ClientConfig()

        self.auth_strategy = auth

        if http_client is not None:
            self.http_client = http_client
        else:
            self.http_client = HTTPClient(
                timeout=self.config.timeout,
                verify_ssl=self.config.verify_ssl,
                max_retries=self.config.max_retries,
                user_agent=self.config.user_agent,
            )

        self._endpoints = resolve_endpoints(self.base_url, endpoints)

    def introspect_token(
        self,
        token: str,
        token_type_hint: str | None = None,
        *,
        timeout: float | None = None,
    ) -> IntrospectionResponse:
        """Introspect an OAuth 2.0 token.

        Simple usage:
            response = client.introspect_token("token_to_check")

        Optimized usage:
            response = client.introspect_token(
                "token_to_check",
                token_type_hint="access_token"
            )

        Args:
            token: The token to introspect
            token_type_hint: Server optimization hint
            timeout: Optional timeout override

        Returns:
            IntrospectionResponse with token metadata and active status
        """
        return introspect_token(
            token=token,
            introspection_endpoint=self._endpoints.introspect,
            auth_strategy=self.auth_strategy,
            http_client=self.http_client,
            token_type_hint=token_type_hint,
            timeout=timeout,
        )

    def register_client(
        self,
        *,
        client_name: str,
        jwks_uri: str | None = None,
        jwks: dict | None = None,
        token_endpoint_auth_method: TokenEndpointAuthMethod
        | str = TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
        redirect_uris: list[str] | None = None,
        grant_types: list[GrantType | str] | None = None,
        response_types: list[ResponseType | str] | None = None,
        scope: str | list[str] | None = None,
        timeout: float | None = None,
        **additional_metadata,
    ) -> ClientRegistrationResponse:
        """Register a new OAuth 2.0 client with the authorization server.

        Simple usage (S2S):
            response = client.register_client(
                client_name="MyService",
                jwks_uri="https://zone1234.keycard.cloud/.well-known/jwks.json"
            )

        Full control:
            response = client.register_client(
                client_name="WebApp",
                redirect_uris=["https://app.com/callback"],
                grant_types=["authorization_code", "refresh_token"],
                scope=["openid", "profile", "email"],
                token_endpoint_auth_method="private_key_jwt"
            )

        Args:
            client_name: Human-readable client name (required)
            jwks_uri: URL pointing to client's JSON Web Key Set
            jwks: Client's JSON Web Key Set (alternative to jwks_uri)
            token_endpoint_auth_method: Client authentication method
            redirect_uris: Client redirect URIs for authorization code flow
            grant_types: OAuth 2.0 grant types the client will use
            response_types: OAuth 2.0 response types the client will use
            scope: Requested scope for the client (string or list)
            timeout: Optional timeout override
            **additional_metadata: Additional client metadata (vendor extensions)

        Returns:
            ClientRegistrationResponse with client credentials and metadata
        """
        return register_client(
            client_name=client_name,
            registration_endpoint=self._endpoints.register,
            auth_strategy=self.auth_strategy,
            http_client=self.http_client,
            jwks_uri=jwks_uri,
            jwks=jwks,
            token_endpoint_auth_method=token_endpoint_auth_method,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            response_types=response_types,
            scope=scope,
            timeout=timeout,
            **additional_metadata,
        )

    def endpoints_summary(self) -> dict[str, dict[str, str]]:
        """Get diagnostic summary of resolved endpoints.

        Returns:
            Dictionary showing resolved URLs and their sources
        """
        return create_endpoints_summary(self._endpoints)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.http_client.close()
