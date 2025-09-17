import asyncio
import contextlib
import inspect
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import ASGIApp

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.http.auth import AuthStrategy, MultiZoneBasicAuth, NoneAuth
from keycardai.oauth.types.models import JsonWebKey, JsonWebKeySet, TokenResponse
from keycardai.oauth.types.oauth import GrantType, TokenEndpointAuthMethod

from ..routers.metadata import protected_mcp_router
from .identity import (
    FilePrivateKeyStorage,
    PrivateKeyIdentityManager,
    PrivateKeyStorageProtocol,
)
from .verifier import TokenVerifier


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


class AuthProvider:
    """KeyCard authentication provider with token exchange capabilities.

    This provider handles both authentication (token verification) and authorization
    (token exchange for resource access) in MCP servers.

    Example:
        ```python
        from keycardai.mcp.server import AuthProvider
        from keycardai.oauth.http.auth import MultiZoneBasicAuth

        # Single zone (default)
        provider = AuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My MCP Server"
        )

        # Multi-zone support with zone-specific credentials
        multi_zone_auth = MultiZoneBasicAuth({
            "zone1": ("client_id_1", "client_secret_1"),
            "zone2": ("client_id_2", "client_secret_2"),
        })

        provider = AuthProvider(
            zone_url="https://keycard.cloud",
            mcp_server_name="My MCP Server",
            auth=multi_zone_auth,
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
        auth: AuthStrategy | None = None,
        enable_multi_zone: bool = False,
        base_url: str | None = None,
        enable_private_key_identity: bool = False,
        private_key_storage: PrivateKeyStorageProtocol | None = None,
        private_key_storage_dir: str | None = None,
    ):
        """Initialize the KeyCard auth provider.

        Args:
            zone_id: KeyCard zone ID for OAuth operations.
            zone_url: KeyCard zone URL for OAuth operations. When enable_multi_zone=True,
                     this should be the top-level domain (e.g., "https://keycard.cloud")
            mcp_server_name: Human-readable name for the MCP server
            required_scopes: Required scopes for token validation
            mcp_server_url: Resource server URL (defaults to server URL)
            auth: Authentication strategy for OAuth operations. For multi-zone scenarios,
                 use MultiZoneBasicAuth to provide zone-specific credentials
            enable_multi_zone: Enable multi-zone support where zone_url is the top-level domain
                              and zone_id is extracted from request context
            enable_private_key_identity: Enable private key JWT authentication
            private_key_storage: Custom storage backend for private keys (optional)
            private_key_storage_dir: Directory for file-based key storage (default: ./mcp_keys)
        """
        if zone_url is None and zone_id is None:
            raise ValueError("zone_url or zone_id is required")

        if zone_url is None:
            if base_url:
                zone_url = f"{AnyHttpUrl(base_url).scheme}://{zone_id}.{AnyHttpUrl(base_url).host}"
            else:
                zone_url = f"https://{zone_id}.keycard.cloud"

        self.zone_url = zone_url
        self.mcp_server_name = mcp_server_name
        self.required_scopes = required_scopes
        self.mcp_server_url = mcp_server_url
        self.client_name = mcp_server_name or "MCP Server OAuth Client"
        self.enable_multi_zone = enable_multi_zone

        self._client: AsyncClient | None = None
        self._init_lock: asyncio.Lock | None = None
        self.auth = auth or NoneAuth()
        if isinstance(self.auth, NoneAuth):
            self.auto_register_client = True
        else:
            self.auto_register_client = False

        self.audience = audience
        self.enable_private_key_identity = enable_private_key_identity

        self._identity_manager: PrivateKeyIdentityManager | None = None
        if enable_private_key_identity:
            if private_key_storage is not None:
                storage = private_key_storage
            else:
                storage_dir = private_key_storage_dir or "./mcp_keys"
                storage = FilePrivateKeyStorage(storage_dir)

            stable_client_id = self.mcp_server_name or f"mcp-server-{generate()}"
            stable_client_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in stable_client_id)

            self._identity_manager = PrivateKeyIdentityManager(
                storage=storage,
                key_id=stable_client_id,
                audience_config=audience
            )

    def _bootstrap_private_key_identity(self):
        """Bootstrap private key identity.

        Idempotent operation which checks if the key pair already exists,
        loads the pair into memory, or creates a private key pair using
        cryptography package and stores the key pair in the configured storage.
        """
        if not self.enable_private_key_identity or self._identity_manager is None:
            return

        self._identity_manager.bootstrap_identity()

    def _extract_auth_info_from_context(
        self, *args, **kwargs
    ) -> tuple[str | None, str | None]:
        """Extract access token and zone_id from FastMCP Context if available.

        Returns:
            Tuple of (access_token, zone_id) or (None, None) if not found
        """
        contexts = []

        for arg in args:
            if isinstance(arg, Context):
                contexts.append(arg)

        for value in kwargs.values():
            if isinstance(value, Context):
                contexts.append(value)

        for ctx in contexts:
            try:
                if (
                    hasattr(ctx, "request_context")
                    and hasattr(ctx.request_context, "request")
                    and hasattr(ctx.request_context.request, "state")
                ):
                    state = ctx.request_context.request.state

                    access_token = None
                    zone_id = None

                    access_token_obj = getattr(state, "access_token", None)
                    if access_token_obj and hasattr(access_token_obj, "token"):
                        access_token = access_token_obj.token

                    zone_id = getattr(state, "zone_id", None)

                    return access_token, zone_id
            except Exception:
                continue

        return None, None

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

    async def _ensure_client_initialized(self, zone_id: str | None = None):
        """Initialize OAuth client if not already done.

        This method provides thread-safe initialization of the OAuth client
        for token exchange operations.

        Args:
            zone_id: Zone ID for multi-zone scenarios. When provided with enable_multi_zone=True,
                    creates zone-specific client for that zone.
        """
        client_key = (
            f"zone:{zone_id}" if self.enable_multi_zone and zone_id else "default"
        )
        if not hasattr(self, "_clients"):
            self._clients: dict[str, AsyncClient | None] = {}

        if client_key in self._clients and self._clients[client_key] is not None:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if client_key in self._clients and self._clients[client_key] is not None:
                return

            try:
                if self.enable_private_key_identity:
                    self._bootstrap_private_key_identity()

                """
                When enable_private_key_identity is True, the client is configured to use the private key identity.
                It uses client registration endpoint but configures itself with jwt authorization strategy
                The client_id is configured to the audience for the zone or by default to the url of the resource
                """
                client_config = ClientConfig(
                    client_name=self.client_name,
                    auto_register_client=self.auto_register_client,
                    enable_metadata_discovery=self.auto_register_client,
                )

                if self.enable_private_key_identity:
                    client_config.client_id = "http://192.168.1.64:8000/.well-known/oauth-protected-resource/5hp9n12kibpg042gwrsvrqiqiv/mcp"
                    client_config.auto_register_client = True
                    client_config.client_jwks_url = self._identity_manager.get_client_jwks_url()
                    client_config.client_token_endpoint_auth_method = TokenEndpointAuthMethod.PRIVATE_KEY_JWT
                    client_config.client_grant_types = [GrantType.CLIENT_CREDENTIALS]

                base_url = self.zone_url
                if self.enable_multi_zone and zone_id:
                    base_url = self._create_zone_scoped_url(self.zone_url, zone_id)

                auth_strategy = self.auth
                if isinstance(self.auth, MultiZoneBasicAuth) and zone_id:
                    if not self.auth.has_zone(zone_id):
                        raise ValueError(
                            f"No credentials configured for zone '{zone_id}'. Available zones: {self.auth.get_configured_zones()}"
                        )
                    auth_strategy = self.auth.get_auth_for_zone(zone_id)

                client = AsyncClient(
                    base_url=base_url,
                    config=client_config,
                    auth=auth_strategy,
                )
                if self.auto_register_client:
                    await client._ensure_initialized()
                self._clients[client_key] = client

                if client_key == "default":
                    self._client = client

            except Exception:
                self._clients[client_key] = None
                if client_key == "default":
                    self._client = None
                raise

    def _get_client(self, zone_id: str | None = None) -> AsyncClient | None:
        """Get the appropriate client for the zone.

        Args:
            zone_id: Zone ID for multi-zone scenarios

        Returns:
            AsyncClient instance for the zone, or None if not initialized
        """
        if not hasattr(self, "_clients"):
            return self._client

        client_key = (
            f"zone:{zone_id}" if self.enable_multi_zone and zone_id else "default"
        )
        return self._clients.get(client_key) or self._client

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
            issuer=self.zone_url,
            enable_multi_zone=enable_multi_zone,
            audience=self.audience,
        )

    def grant(self, resources: str | list[str]):
        """Decorator for automatic delegated token exchange.

        This decorator automates the OAuth token exchange process for accessing
        external resources on behalf of authenticated users. The decorated function
        will receive an AccessContext parameter that provides access to exchanged tokens.

        Args:
            resources: Target resource URL(s) for token exchange.
                      Can be a single string or list of strings.
                      (e.g., "https://api.example.com" or
                       ["https://api.example.com", "https://other-api.com"])

        Usage:
            ```python
            from mcp.server.fastmcp import Context
            # Async function
            @provider.grant("https://api.example.com")
            async def my_async_tool(ctx: AccessContext, request_ctx: Context, user_id: str):
                token = ctx.access("https://api.example.com").access_token
                # Use token to call the external API
                headers = {"Authorization": f"Bearer {token}"}
                # ... make API call

            # Sync function (also supported)
            @provider.grant("https://api.example.com")
            def my_sync_tool(ctx: AccessContext, request_ctx: Context, user_id: str):
                token = ctx.access("https://api.example.com").access_token
                # Use token to call the external API
                headers = {"Authorization": f"Bearer {token}"}
                # ... make API call
            ```

        The decorated function must:
        - Have a parameter annotated with `AccessContext` type (e.g., `my_ctx: AccessContext = None`)
        - Have a parameter annotated with `Context` type from FastMCP (e.g., `request_ctx: Context`)
        - Can be either async or sync (the decorator handles both cases)

        Note: The `Context` parameter is required for accessing request authentication information.
        Without it, the decorator cannot extract the user's authentication token.

        Error handling:
        - Returns structured error response if token exchange fails
        - Preserves original function signature and behavior
        """

        def decorator(func: Callable) -> Callable:
            original_sig = inspect.signature(func)
            new_params = []
            access_ctx_param_name = None

            for param in original_sig.parameters.values():
                if param.annotation == AccessContext or str(param.annotation).replace(
                    " ", ""
                ) in ["AccessContext", "AccessContext|None", "Optional[AccessContext]"]:
                    access_ctx_param_name = param.name
                    continue
                new_params.append(param)

            new_sig = original_sig.replace(parameters=new_params)

            # mcp.server.fastmcp always run in async mode
            is_async_func = inspect.iscoroutinefunction(func)

            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                try:
                    # Extract token and zone_id from FastMCP Context if available
                    user_token, zone_id = self._extract_auth_info_from_context(
                        *args, **kwargs
                    )

                    # Fallback to MCP's get_access_token if no FastMCP context found
                    if not user_token:
                        user_token_obj = get_access_token()
                        user_token = user_token_obj.token if user_token_obj else None

                    # For multi-zone, zone_id is required
                    if self.enable_multi_zone and not zone_id:
                        return {
                            "error": "Zone ID is required for multi-zone configuration but not found in request.",
                            "isError": True,
                            "errorType": "missing_zone_id",
                        }

                    await self._ensure_client_initialized(zone_id)

                    client = self._get_client(zone_id)
                    if client is None:
                        return {
                            "error": "OAuth client not available. Server configuration issue.",
                            "isError": True,
                            "errorType": "server_configuration",
                        }

                    if not user_token:
                        return {
                            "error": "No authentication token available. Please ensure you're properly authenticated.",
                            "isError": True,
                            "errorType": "authentication_required",
                        }

                    resource_list = (
                        [resources] if isinstance(resources, str) else resources
                    )

                    access_tokens = {}
                    for resource in resource_list:
                        try:
                            token_response = await client.exchange_token(
                                subject_token=user_token,
                                resource=resource,
                                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                                client_assertion_type=GrantType.JWT_BEARER_CLIENT_ASSERTION,
                                client_assertion=self._identity_manager.create_client_assertion(f"http://192.168.1.64:8000/.well-known/oauth-protected-resource/{zone_id}/mcp", zone_id),
                            )
                            access_tokens[resource] = token_response
                        except Exception as e:
                            return {
                                "error": f"Token exchange failed for {resource}: {e}",
                                "isError": True,
                                "errorType": "exchange_token_failed",
                                "resource": resource,
                            }

                    access_ctx = AccessContext(access_tokens)
                    if access_ctx_param_name:
                        kwargs[access_ctx_param_name] = access_ctx

                    if is_async_func:
                        return await func(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)

                except Exception as e:
                    return {
                        "error": f"Unexpected error in delegated token exchange: {e}",
                        "isError": True,
                        "errorType": "unexpected_error",
                        "resources": resource_list
                        if "resource_list" in locals()
                        else resources,
                    }

            wrapper.__signature__ = new_sig
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

        verifier = self.get_token_verifier()

        jwks = None
        if self.enable_private_key_identity and self._identity_manager is not None:
            try:
                self._bootstrap_private_key_identity()
                jwks_dict = self._identity_manager.get_public_jwks()
                jwk_objects = []
                for jwk_data in jwks_dict["keys"]:
                    jwk_objects.append(JsonWebKey(**jwk_data))
                jwks = JsonWebKeySet(keys=jwk_objects)
            except Exception as e:
                raise ValueError(f"Error getting JWKS: {e}") from e

        return protected_mcp_router(
            issuer=self.zone_url,
            mcp_app=mcp_app,
            verifier=verifier,
            enable_multi_zone=self.enable_multi_zone,
            jwks=jwks
        )

    def app(self, mcp_app: FastMCP) -> ASGIApp:
        """Get the MCP app with authentication middleware and metadata endpoints."""
        @contextlib.asynccontextmanager
        async def lifespan(app: Starlette):
            async with contextlib.AsyncExitStack() as stack:
                await stack.enter_async_context(mcp_app.session_manager.run())
                yield
        return Starlette(
            routes=self.get_mcp_router(mcp_app.streamable_http_app()),
            lifespan=lifespan,
        )
