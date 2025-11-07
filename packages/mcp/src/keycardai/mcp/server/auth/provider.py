import asyncio
import contextlib
import inspect
import os
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from mcp.shared.context import RequestContext
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Route
from starlette.types import ASGIApp

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.http.auth import MultiZoneBasicAuth, NoneAuth
from keycardai.oauth.types.models import (
    JsonWebKeySet,
    TokenExchangeRequest,
    TokenResponse,
)

from ..exceptions import (
    AuthProviderConfigurationError,
    MissingAccessContextError,
    MissingContextError,
    ResourceAccessError,
)
from ..routers.metadata import protected_mcp_router
from .application_credentials import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from .client_factory import ClientFactory, DefaultClientFactory
from .verifier import TokenVerifier


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
    """Keycard authentication provider with token exchange capabilities.

    This provider handles both authentication (token verification) and authorization
    (token exchange for resource access) in MCP servers.

    Example:
        ```python
        from keycardai.mcp.server import AuthProvider
        from keycardai.mcp.server.auth import ClientSecret

        # Single zone (default) - no credentials required
        provider = AuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My MCP Server"
        )

        # Single zone with client credentials
        client_secret = ClientSecret(
            ("client_id_from_keycard", "client_secret_from_keycard")
        )
        provider = AuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My MCP Server",
            application_credential=client_secret
        )

        # Multi-zone support with zone-specific credentials
        client_secret = ClientSecret({
            "zone1": ("client_id_1", "client_secret_1"),
            "zone2": ("client_id_2", "client_secret_2"),
        })
        provider = AuthProvider(
            zone_url="https://keycard.cloud",
            mcp_server_name="My MCP Server",
            application_credential=client_secret,
            enable_multi_zone=True
        )

        @provider.grant("https://api.example.com")
        async def my_tool(ctx, access_ctx: AccessContext = None):
            token = access_ctx.access("https://api.example.com").access_token
            # Use token to call API
        ```
    """

    def __init__(
        self,
        zone_id: str | None = None,
        zone_url: str | None = None,
        mcp_server_name: str | None = None,
        required_scopes: list[str] | None = None,
        audience: str | dict[str, str] | None = None,
        mcp_server_url: AnyHttpUrl | str | None = None,
        enable_multi_zone: bool = False,
        base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        enable_dynamic_client_registration: bool | None = None,
        application_credential: ApplicationCredential | None = None,
    ):
        """Initialize the Keycard auth provider.

        Args:
            zone_id: Keycard zone ID for OAuth operations.
            zone_url: Keycard zone URL for OAuth operations. When enable_multi_zone=True,
                     this should be the top-level domain (e.g., "https://keycard.cloud")
            mcp_server_name: Human-readable name for the MCP server
            required_scopes: Required scopes for token validation
            audience: Expected token audience for verification. Can be:
                     - str: Single audience value for all zones
                     - dict[str, str]: Zone-specific audience mapping (zone_id -> audience)
                     - None: Skip audience validation (not recommended for production)
            mcp_server_url: Resource server URL (defaults to server URL)
            enable_multi_zone: Enable multi-zone support where zone_url is the top-level domain
                              and zone_id is extracted from request context
            base_url: Base URL for Keycard (default: https://keycard.cloud)
            client_factory: Client factory for creating OAuth clients. Defaults to DefaultClientFactory
            enable_dynamic_client_registration: Override automatic client registration behavior
            application_credential: Workload credential provider for token exchange. Use ClientSecret
                                for Keycard-issued credentials, WebIdentity for private key JWT,
                                or None for basic token exchange without client authentication.
        """
        # Discover configuration from environment variables with explicit parameters taking priority
        zone_id = zone_id or os.getenv("KEYCARD_ZONE_ID")
        zone_url = zone_url or os.getenv("KEYCARD_ZONE_URL")
        base_url = base_url or os.getenv("KEYCARD_BASE_URL")
        mcp_server_url = mcp_server_url or os.getenv("MCP_SERVER_URL")

        self.base_url = base_url or "https://keycard.cloud"

        if zone_url is None and not enable_multi_zone:
            if zone_id is None:
                raise AuthProviderConfigurationError()
            zone_url = f"{AnyHttpUrl(self.base_url).scheme}://{zone_id}.{AnyHttpUrl(self.base_url).host}"
        self.zone_url = zone_url
        # issuer is the URL used to validate tokens.
        # When enable_multi_zone is True, it must be the top-level domain. Zones are inferred from the request.
        self.issuer = self.zone_url or self.base_url
        self.mcp_server_name = mcp_server_name
        self.required_scopes = required_scopes
        self.mcp_server_url = mcp_server_url
        self.client_name = mcp_server_name or "MCP Server OAuth Client"
        self.enable_multi_zone = enable_multi_zone
        self.client_factory = client_factory or DefaultClientFactory()
        self.enable_dynamic_client_registration = enable_dynamic_client_registration

        self._clients: dict[str, AsyncClient | None] = {}

        self._init_lock: asyncio.Lock | None = None
        self.audience = audience

        # Initialize application credential provider with automatic discovery
        self.application_credential = self._discover_application_credential(application_credential)

        # Get the auth strategy for the HTTP client doing the token exchange
        if self.application_credential is not None:
            self.auth = self.application_credential.get_http_client_auth()
        else:
            self.auth = NoneAuth()

        # Extract JWKS if provider supports it (for WebIdentity)
        self.jwks: JsonWebKeySet | None = None
        if self.application_credential and hasattr(self.application_credential, 'get_jwks'):
            self.jwks = self.application_credential.get_jwks()

        # Backward compatibility: detect if using WebIdentity
        self.enable_private_key_identity = isinstance(self.application_credential, WebIdentity)

    def _discover_application_credential(self, application_credential: ApplicationCredential | None) -> ApplicationCredential | None:
        """Discover the application credential from the provided parameters.

        Args:
            application_credential: Application credential to discover

        Returns:
            ApplicationCredential: The discovered application credential
        """
        if application_credential is not None:
            return application_credential

        # discover environment variables
        client_id = os.getenv("KEYCARD_CLIENT_ID")
        client_secret = os.getenv("KEYCARD_CLIENT_SECRET")
        if client_id and client_secret:
            return ClientSecret((client_id, client_secret))

        application_credential_type = os.getenv("KEYCARD_APPLICATION_CREDENTIAL_TYPE")
        if application_credential_type == "eks_workload_identity":
            custom_token_file_path = os.getenv("KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE")
            return EKSWorkloadIdentity(token_file_path=custom_token_file_path)
        elif application_credential_type == "web_identity":
            key_storage_dir = os.getenv("KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR")
            return WebIdentity(
                mcp_server_name=self.mcp_server_name,
                storage_dir=key_storage_dir,
            )
        elif application_credential_type is not None:
            raise AuthProviderConfigurationError(
                message=f"Unknown application credential type: {application_credential_type}. Supported types: eks_workload_identity, web_identity"
            )

        # detect workload identity from environment variables
        if any(os.getenv(env_name) for env_name in EKSWorkloadIdentity.default_env_var_names):
            return EKSWorkloadIdentity()

        return None

    def _create_zone_scoped_url(self, base_url: str, zone_id: str) -> str:
        """Create zone-scoped URL by prepending zone_id to the host."""
        base_url_obj = AnyHttpUrl(base_url)

        port_part = ""
        if base_url_obj.port and not (
            (base_url_obj.scheme == "https" and base_url_obj.port == 443)
            or (base_url_obj.scheme == "http" and base_url_obj.port == 80)
        ):
            port_part = f":{base_url_obj.port}"

        zone_url = f"{base_url_obj.scheme}://{zone_id}.{base_url_obj.host}{port_part}"
        return zone_url

    def _get_client_key(self, zone_id: str | None = None) -> str:
        """Get the client key for the given auth info."""
        if self.enable_multi_zone and zone_id:
            return f"zone:{zone_id}"
        return "default"

    async def _get_or_create_client(self, auth_info: dict[str, str] | None = None) -> AsyncClient | None:
        """
        This method is executed in request context.
        Global lock is used to ensure that only one client is created for zone.
        """
        client = None
        client_key = self._get_client_key(auth_info["zone_id"])
        if client_key in self._clients and self._clients[client_key] is not None:
            return self._clients[client_key]

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if client_key in self._clients and self._clients[client_key] is not None:
                return self._clients[client_key]

            try:
                client_config = ClientConfig(
                    client_name=self.client_name,
                    enable_metadata_discovery=True,
                )

                # Let the credential provider configure client settings
                # This keeps credential-specific configuration encapsulated
                if self.application_credential:
                    client_config = self.application_credential.set_client_config(client_config, auth_info)

                # Determine the correct base URL for the OAuth client
                # Single-zone: use self.zone_url (already includes zone_id in hostname)
                # Multi-zone: construct zone-scoped URL from base_url + zone_id from request
                if self.enable_multi_zone and auth_info['zone_id']:
                    base_url = self._create_zone_scoped_url(self.base_url, auth_info['zone_id'])
                else:
                    base_url = self.zone_url

                auth_strategy = self.auth
                if isinstance(self.auth, MultiZoneBasicAuth) and auth_info['zone_id']:
                    if not self.auth.has_zone(auth_info['zone_id']):
                        raise AuthProviderConfigurationError()
                    auth_strategy = self.auth.get_auth_for_zone(auth_info['zone_id'])

                # Configure dynamic client registration
                # Priority: explicit config > identity provider defaults
                if self.enable_dynamic_client_registration is not None:
                    # Explicit configuration always takes precedence
                    client_config.auto_register_client = self.enable_dynamic_client_registration
                elif self.application_credential is None and isinstance(auth_strategy, NoneAuth):
                    # For basic token exchange with no authentication, enable registration by default
                    # Other credential providers (WebIdentity, ClientSecret) handle their own defaults
                    client_config.auto_register_client = True

                client = self.client_factory.create_async_client(
                    base_url=base_url,
                    auth=auth_strategy,
                    config=client_config
                )
            finally:
                self._clients[client_key] = client
            return client

    def _get_client(self, zone_id: str | None = None) -> AsyncClient | None:
        """Get the appropriate client for the zone.

        Args:
            zone_id: Zone ID for multi-zone scenarios

        Returns:
            AsyncClient instance for the zone, or None if not initialized
        """
        client_key = self._get_client_key(zone_id)
        return self._clients.get(client_key)

    def get_auth_settings(self) -> AuthSettings:
        """Get authentication settings for the MCP server."""
        return AuthSettings.model_validate(
            {
                "issuer_url": self.zone_url,
                "resource_server_url": self.mcp_server_url,
                "required_scopes": self.required_scopes,
            }
        )

    def get_token_verifier(
        self, enable_multi_zone: bool | None = None
    ) -> TokenVerifier:
        """Get a token verifier for the MCP server."""
        if enable_multi_zone is None:
            enable_multi_zone = self.enable_multi_zone
        return TokenVerifier(
            required_scopes=self.required_scopes,
            issuer=self.issuer,
            enable_multi_zone=enable_multi_zone,
            audience=self.audience,
            client_factory=self.client_factory,
        )

    def grant(self, resources: str | list[str]):
        """Decorator for automatic delegated token exchange.

        This decorator automates the OAuth token exchange process for accessing
        external resources on behalf of authenticated users. The decorated function
        will receive an AccessContext parameter that provides access to exchanged tokens.

        The decorator avoids raising exceptions, and instead sets the error state in the AccessContext.

        Args:
            resources: Target resource URL(s) for token exchange.
                      Can be a single string or list of strings.
                      (e.g., "https://api.example.com" or
                       ["https://api.example.com", "https://other-api.com"])

        Usage:
            ```python
            from mcp.server.fastmcp import Context
            from keycardai.mcp.server.auth import AccessContext

            @provider.grant("https://api.example.com")
            def my_tool(access_ctx: AccessContext, ctx: Context, user_id: str):
                # Check for errors first
                if access_ctx.has_errors():
                    print("Failed to obtain access token for resource")
                    print(f"Error: {access_ctx.get_errors()}")
                    return

                # Access token for successful resources
                token = access_ctx.access("https://api.example.com").access_token
                headers = {"Authorization": f"Bearer {token}"}
                # Use headers to call external API
                return f"Data for {user_id}"

            # Also works with async functions
            @provider.grant("https://api.example.com")
            async def my_async_tool(access_ctx: AccessContext, ctx: Context, user_id: str):
                if access_ctx.has_errors():
                    return {"error": "Token exchange failed"}
                token = access_ctx.access("https://api.example.com").access_token
                # Async API call
                return f"Async data for {user_id}"
            ```

        The decorated function must:
        - Have a parameter annotated with `AccessContext` type (e.g., `access_ctx: AccessContext`)
        - Have a parameter annotated with `Context` type from MCP (e.g., `ctx: Context`)
        - Can be either async or sync (the decorator handles both cases automatically)

        Error handling:
        - Sets error state in AccessContext if token exchange fails
        - Preserves original function signature and behavior
        - Provides detailed error messages for debugging
        """
        """
        Return the name and the index of the parameter with the given type.
        Used to locatethe AccessContext parameter.
        """
        def _get_param_info_by_type(func: Callable, param_type: type) -> tuple[str, int] | None:
            sig = inspect.signature(func)
            for index, value in enumerate(sig.parameters.values()):
                if value.annotation == param_type:
                    return value.name, index
            return None

        """
        @mcp.tool() decorator uses function signatures to construct tool schemas.
        The access context is not supposed to be added to the LLM tool call signature.
        This helper returns tool func signature without the AccessContext parameter.
        """
        def _get_safe_func_signature(func: Callable) -> inspect.Signature:
            sig = inspect.signature(func)
            safe_params = []
            for param in sig.parameters.values():
                if param.annotation == AccessContext:
                    continue
                safe_params.append(param)
            return sig.replace(parameters=safe_params)

        """
        Return the Context parameter from the function arguments.
        """
        def _get_context(*args, **kwargs) -> Context | None:
            for value in args:
                if isinstance(value, Context):
                    return value
            for value in kwargs.values():
                if isinstance(value, Context):
                    return value
            return None

        """
        Return the RequestContext parameter from the function arguments.
        """
        def _get_request_context(*args, **kwargs) -> RequestContext | None:
            for value in args:
                if isinstance(value, RequestContext):
                    return value
            for value in kwargs.values():
                if isinstance(value, RequestContext):
                    return value
            return None

        """
        The auth info object is set by the bearer token middleware during authorization.
        This helper extracts the auth info from the request context from either the RequestContext or the Context parameter.
        Required to support both FastMCP and lowlevel server implementations.
        """
        def _extract_auth_info_from_context(
            *args, **kwargs
        ) -> dict[str, str] | None:
            """Use _var naming to avoid clashing with the args, kwargs."""
            _request_context = _get_request_context(*args, **kwargs)
            if _request_context is None:
                _fastmcp_context = _get_context(*args, **kwargs)
                if _fastmcp_context is not None:
                    _request_context = _fastmcp_context.request_context
            if _request_context is None:
                return None
            try:
                return _request_context.request.state.keycardai_auth_info
            except Exception:
                return None

        """
        Helper to set error context on the AccessContext.
        """
        def _set_error(error: dict[str, str], resource: str | None, access_context: AccessContext):
            """Helper to set error context."""
            if resource:
                access_context.set_resource_error(resource, error)
            else:
                access_context.set_error(error)           # mcp.server.fastmcp always runs in async mode

        """
        The mcp package always runs in async mode, however users can supply functions with either sync or async signature.
        This helper calls the function with the appropriate signature.
        """
        async def _call_func(_is_async_func: bool, func: Callable, *args, **kwargs):
            if _is_async_func:
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)

        def _is_access_ctx_in_args(access_ctx_param_index: int, args: tuple) -> bool:
            return access_ctx_param_index < len(args)

        def _get_access_ctx_from_args(_access_ctx_param_index: str, *args) -> tuple:
            if isinstance(args[_access_ctx_param_index], AccessContext):
                return args, args[_access_ctx_param_index]
            _new_args = (*args[:_access_ctx_param_index], AccessContext(), *args[_access_ctx_param_index + 1:])
            return _new_args, _new_args[_access_ctx_param_index]

        def decorator(func: Callable) -> Callable:
            _is_async_func = inspect.iscoroutinefunction(func)
            if _get_param_info_by_type(func, Context) is None:
                if _get_param_info_by_type(func, RequestContext) is None:
                    raise MissingContextError()

            _access_ctx_param_info = _get_param_info_by_type(func, AccessContext)
            if _access_ctx_param_info is None:
                raise MissingAccessContextError()
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                _access_ctx = None
                if _is_access_ctx_in_args(_access_ctx_param_info[1], args):
                    args, _access_ctx = _get_access_ctx_from_args(_access_ctx_param_info[1], *args)
                elif _access_ctx_param_info[0] not in kwargs or kwargs[_access_ctx_param_info[0]] is None:
                    kwargs[_access_ctx_param_info[0]] = AccessContext()
                    _access_ctx = kwargs[_access_ctx_param_info[0]]
                else:
                    _access_ctx = kwargs[_access_ctx_param_info[0]]
                _keycardai_auth_info: dict[str, str] | None = None
                try:
                    _keycardai_auth_info = _extract_auth_info_from_context(*args, **kwargs)
                    if not _keycardai_auth_info:
                        _set_error({
                            "error": "No request authentication information available. Ensure the provider is correctly configured.",
                        }, None, _access_ctx)
                        return await _call_func(_is_async_func, func, *args, **kwargs)

                    if not _keycardai_auth_info["access_token"]:
                        _set_error({
                            "error": "No authentication token available. Please ensure you're properly authenticated.",
                        }, None, _access_ctx)
                        return await _call_func(_is_async_func, func, *args, **kwargs)
                except Exception as e:
                    _set_error({
                        "error": "Failed to get access token from context. Ensure the Context parameter is properly annotated.",
                        "raw_error": str(e),
                    }, None, _access_ctx)
                    return await _call_func(_is_async_func, func, *args, **kwargs)
                _client = None
                if self.enable_multi_zone and not _keycardai_auth_info["zone_id"]:
                    _set_error({
                        "error": "Zone ID is required for multi-zone configuration but not found in request.",
                    }, None, _access_ctx)
                    return await _call_func(_is_async_func, func, *args, **kwargs)
                try:
                    _client = await self._get_or_create_client(_keycardai_auth_info)
                    if _client is None:
                        _set_error({
                            "error": "OAuth client not available. Server configuration issue.",
                        }, None, _access_ctx)
                        return await _call_func(_is_async_func, func, *args, **kwargs)
                except Exception as e:
                    _set_error({
                        "error": "Failed to initialize OAuth client. Server configuration issue.",
                        "raw_error": str(e),
                    }, None, _access_ctx)
                    return await _call_func(_is_async_func, func, *args, **kwargs)
                _resource_list = (
                    [resources] if isinstance(resources, str) else resources
                )
                _access_tokens = {}
                for resource in _resource_list:
                    try:
                        # Prepare token exchange request using application identity provider
                        if self.application_credential:
                            _token_exchange_request = await self.application_credential.prepare_token_exchange_request(
                                client=_client,
                                subject_token=_keycardai_auth_info["access_token"],
                                resource=resource,
                                auth_info=_keycardai_auth_info,
                            )
                        else:
                            # Basic token exchange without client authentication
                            _token_exchange_request = TokenExchangeRequest(
                                subject_token=_keycardai_auth_info["access_token"],
                                resource=resource,
                                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                            )

                        # Execute token exchange
                        _token_response = await _client.exchange_token(_token_exchange_request)
                        _access_tokens[resource] = _token_response
                    except Exception as e:
                        _error_message = f"Token exchange failed for {resource}"
                        if self.enable_private_key_identity and _keycardai_auth_info.get("resource_client_id"):
                            _error_message += f" with client id: {_keycardai_auth_info['resource_client_id']}"
                        _error_message += f": {e}"

                        _set_error({
                            "error": _error_message,
                            "raw_error": str(e),
                        }, resource, _access_ctx)

                # Set successful tokens on the existing access_context (preserves any resource errors)
                _access_ctx.set_bulk_tokens(_access_tokens)
                return await _call_func(_is_async_func, func, *args, **kwargs)
            wrapper.__signature__ = _get_safe_func_signature(func)
            return wrapper
        return decorator

    def get_mcp_router(self, mcp_app: ASGIApp) -> Sequence[Route]:
        """Get MCP router with authentication middleware and metadata endpoints.

        This method creates the complete routing structure for a protected MCP server,
        including OAuth metadata endpoints and the main MCP application with authentication.

        Args:
            mcp_app: The MCP FastMCP streamable HTTP application

        Returns:
            Sequence of routes including metadata mount and protected MCP mount

        Example:
            ```python
            from starlette.applications import Starlette

            # Create MCP server and auth provider
            mcp = FastMCP("My Server")
            provider = AuthProvider(zone_url="https://keycard.cloud", ...)

            # Create Starlette app with protected routes
            app = Starlette(routes=provider.get_mcp_router(mcp.streamable_http_app()))
            ```
        """
        return protected_mcp_router(
            issuer=self.issuer,
            mcp_app=mcp_app,
            verifier=self.get_token_verifier(),
            enable_multi_zone=self.enable_multi_zone,
            jwks=self.jwks
        )

    def app(self, mcp_app: FastMCP, middleware: list[Middleware] | None = None) -> ASGIApp:
        """Get the MCP app with authentication middleware and metadata endpoints."""
        @contextlib.asynccontextmanager
        async def lifespan(app: Starlette):
            async with contextlib.AsyncExitStack() as stack:
                await stack.enter_async_context(mcp_app.session_manager.run())
                yield
        return Starlette(
            routes=self.get_mcp_router(mcp_app.streamable_http_app()),
            lifespan=lifespan,
            middleware=middleware,
        )
