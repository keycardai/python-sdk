import asyncio
import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from keycardai.oauth import AsyncClient, AuthStrategy, Client, ClientConfig, NoneAuth
from keycardai.oauth.types.models import TokenResponse

from .verifier import KeycardTokenVerifier


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
            raise KeyError(f"Resource '{resource}' not granted. Available resources: {list(self._access_tokens.keys())}")
        return self._access_tokens[resource]

class KeycardAuthProvider:
    """KeyCard authentication provider with token exchange capabilities.

    This provider handles both authentication (token verification) and authorization
    (token exchange for resource access) in MCP servers.

    Example:
        ```python
        from keycardai.mcp.server import KeycardAuthProvider

        provider = KeycardAuthProvider(
            zone_url="https://abc1234.keycard.cloud",
            mcp_server_name="My MCP Server"
        )

        @provider.grant("https://api.example.com")
        async def my_tool(ctx, access_ctx: AccessContext = None):
            token = access_ctx.access("https://api.example.com").access_token
            # Use token to call API
        ```
    """

    def __init__(self,
        zone_url: str,
        mcp_server_name: str | None = None,
        required_scopes: list[str] | None = None,
        mcp_server_url: AnyHttpUrl | str | None = None,
        client_name: str | None = None,
        auth: AuthStrategy = NoneAuth):
        """Initialize the KeyCard auth provider.

        Args:
            zone_url: KeyCard zone URL for OAuth operations
            mcp_server_name: Human-readable name for the MCP server
            required_scopes: Required scopes for token validation
            mcp_server_url: Resource server URL (defaults to server URL)
            client_name: OAuth client name for registration (defaults to mcp_server_name)
        """
        self.zone_url = zone_url
        self.mcp_server_name = mcp_server_name
        self.required_scopes = required_scopes
        self.mcp_server_url = mcp_server_url
        self.client_name = client_name or mcp_server_name or "MCP Server OAuth Client"

        self._client: AsyncClient | None = None
        self._init_lock: asyncio.Lock | None = None
        self.auth = auth
        if isinstance(auth, NoneAuth):
            self.auto_register_client = True
        else:
            self.auto_register_client = False

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

    def get_auth_settings(self) -> AuthSettings:
        """Get authentication settings for the MCP server."""
        return AuthSettings.model_validate(
            {
                "issuer_url": self.zone_url,
                "resource_server_url": self.mcp_server_url,
                "required_scopes": self.required_scopes,
            }
        )

    def get_token_verifier(self) -> KeycardTokenVerifier:
        """Get a token verifier for the MCP server."""
        with Client(self.zone_url) as client:
            jwks_uri = client.discover_server_metadata().jwks_uri
        return KeycardTokenVerifier(
            required_scopes=self.required_scopes,
            issuer=self.zone_url,
            jwks_uri=jwks_uri,
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
            @provider.grant("https://api.example.com")
            async def my_tool(ctx: AccessContext, user_id: str):
                token = ctx.access("https://api.example.com").access_token
                # Use token to call the external API
                headers = {"Authorization": f"Bearer {token}"}
                # ... make API call
            ```

        The decorated function must:
        - Have a parameter annotated with `AccessContext` type (e.g., `my_ctx: AccessContext = None`)
        - Be async (token exchange is async)

        Error handling:
        - Returns structured error response if token exchange fails
        - Preserves original function signature and behavior
        """
        def decorator(func: Callable) -> Callable:
            original_sig = inspect.signature(func)
            new_params = []
            access_ctx_param_name = None

            for param in original_sig.parameters.values():
                if (param.annotation == AccessContext or
                    str(param.annotation).replace(' ', '') in ['AccessContext', 'AccessContext|None', 'Optional[AccessContext]']):
                    access_ctx_param_name = param.name
                    continue
                new_params.append(param)

            new_sig = original_sig.replace(parameters=new_params)

            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                try:
                    await self._ensure_client_initialized()

                    if self._client is None:
                        return {
                            "error": "OAuth client not available. Server configuration issue.",
                            "isError": True,
                            "errorType": "server_configuration",
                        }

                    user_token_obj = get_access_token()
                    user_token = user_token_obj.token if user_token_obj else None

                    if not user_token:
                        return {
                            "error": "No authentication token available. Please ensure you're properly authenticated.",
                            "isError": True,
                            "errorType": "authentication_required",
                        }

                    resource_list = [resources] if isinstance(resources, str) else resources

                    access_tokens = {}
                    for resource in resource_list:
                        try:
                            token_response = await self._client.exchange_token(
                                subject_token=user_token,
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

                    access_ctx = AccessContext(access_tokens)
                    if access_ctx_param_name:
                        kwargs[access_ctx_param_name] = access_ctx

                    return await func(*args, **kwargs)

                except Exception as e:
                    return {
                        "error": f"Unexpected error in delegated token exchange: {e}",
                        "isError": True,
                        "errorType": "unexpected_error",
                        "resources": resource_list if 'resource_list' in locals() else resources,
                    }

            wrapper.__signature__ = new_sig
            return wrapper
        return decorator

