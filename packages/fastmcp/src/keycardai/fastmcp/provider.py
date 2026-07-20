"""Keycard authentication provider for FastMCP.

This module provides AuthProvider, which integrates Keycard's OAuth
token verification with FastMCP's authentication system. The AuthProvider
creates a RemoteAuthProvider instance with automatic Keycard zone discovery
and JWT token verification.
"""

from __future__ import annotations

import inspect
import logging
import os
import warnings
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints
from urllib.parse import urlparse

from pydantic import AnyHttpUrl

from fastmcp import Context
from fastmcp.dependencies import Dependency
from fastmcp.server.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token, get_context
from keycardai.mcp.server.auth import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from keycardai.mcp.server.auth.client_factory import (
    ClientFactory,
    DefaultClientFactory,
)
from keycardai.mcp.server.exceptions import (
    AuthProviderConfigurationError,
    AuthProviderInternalError,
    AuthProviderRemoteError,
    MissingContextError,
    ResourceAccessError,
)
from keycardai.oauth import AsyncClient, Client
from keycardai.oauth.http.auth import NoneAuth
from keycardai.oauth.types.models import TokenExchangeRequest, TokenResponse
from keycardai.oauth.utils.jwt import extract_scopes, get_claims

__all__ = [
    "INTROSPECT",
    "AccessContext",
    "AnyHttpUrl",
    "ApplicationCredential",
    "AsyncClient",
    "AuthProvider",
    "AuthProviderConfigurationError",
    "AuthProviderInternalError",
    "AuthProviderRemoteError",
    "Client",
    "ClientFactory",
    "ClientSecret",
    "Context",
    "DefaultClientFactory",
    "EKSWorkloadIdentity",
    "GrantDependency",
    "JWTVerifier",
    "MissingContextError",
    "NoneAuth",
    "RemoteAuthProvider",
    "ResourceAccessError",
    "TokenExchangeRequest",
    "TokenResponse",
    "WebIdentity",
    "extract_scopes",
    "get_access_token",
    "get_claims",
    "get_token_debug_info",
    "introspect",
    "override_access_context",
]

logger = logging.getLogger(__name__)

# Define custom INTROSPECT log level for detailed token debugging
# INTROSPECT (5) is more detailed than DEBUG (10) - use for sensitive token introspection
INTROSPECT = 5
logging.addLevelName(INTROSPECT, "INTROSPECT")

def introspect(self, message, *args, **kwargs):
    """Log at INTROSPECT level - most detailed debugging including token info."""
    if self.isEnabledFor(INTROSPECT):
        self._log(INTROSPECT, message, args, **kwargs)

# Add introspect method to Logger class
logging.Logger.introspect = introspect

# Configure logger to respect KEYCARD_LOG_LEVEL environment variable
_log_level = os.getenv("KEYCARD_LOG_LEVEL", "").upper()
if _log_level in ("INTROSPECT", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    if _log_level == "INTROSPECT":
        logger.setLevel(INTROSPECT)
    else:
        logger.setLevel(getattr(logging, _log_level))

    if not logger.handlers:
        _handler = logging.StreamHandler()
        # Match FastMCP's logging format for consistency
        # Format: [MM/DD/YY HH:MM:SS] LEVEL    Message    filename:line
        _handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)-8s %(message)s    %(filename)s:%(lineno)d",
                datefmt="%m/%d/%y %H:%M:%S"
            )
        )
        logger.addHandler(_handler)


def get_token_debug_info(access_token: str) -> dict[str, Any]:
    """Extract non-sensitive debugging information from a JWT access token.

    This function safely extracts only non-sensitive claims from a JWT token
    for debugging and logging purposes. It does NOT verify the token signature
    and only returns issuer, audience, subject, and scope information.

    **Important:** This is a debug function that NEVER raises exceptions. If token
    parsing fails, it returns an error indicator in the result dict instead.

    **Security Note:** This function is designed for internal debugging and logging.
    While it includes subject (user identifier), it excludes:
    - The actual token string
    - Custom claims that might contain PII

    Args:
        access_token: JWT access token string (without Bearer prefix)

    Returns:
        Dictionary with token information:
        - issuer (str): Token issuer (if present)
        - audience (str | list[str]): Token audience (if present)
        - subject (str): Token subject/user identifier (if present)
        - expires_at (int): Token expiration time as Unix timestamp (if present)
        - issued_at (int): Token issuance time as Unix timestamp (if present)
        - scopes (list[str]): List of scopes from the token (if present)
        - error (str): Error message if token parsing failed

    Example:
        >>> token = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9..."
        >>> debug_info = get_token_debug_info(token)
        >>> logger.info(f"Token info: {debug_info}")
        # Success: {"issuer": "https://auth.example.com", "audience": "api.example.com", "subject": "user123", "expires_at": 1700000000, "issued_at": 1699996400, "scopes": ["read", "write"]}
        # Failure: {"error": "Failed to parse token"}
    """
    try:
        claims = get_claims(access_token)

        debug_info: dict[str, Any] = {}

        if "iss" in claims:
            debug_info["issuer"] = claims["iss"]

        if "aud" in claims:
            debug_info["audience"] = claims["aud"]

        if "sub" in claims:
            debug_info["subject"] = claims["sub"]

        if "exp" in claims:
            debug_info["expires_at"] = claims["exp"]

        if "iat" in claims:
            debug_info["issued_at"] = claims["iat"]

        scopes = extract_scopes(claims)
        if scopes:
            debug_info["scopes"] = scopes

        return debug_info

    except Exception:
        return {"error": "Failed to parse token"}


# FastMCP context state key under which the AccessContext is stored.
# Reading it via ctx.get_state(KEYCARD_STATE_KEY) is deprecated in favor of
# declaring an AccessContext parameter; the key remains written during the
# deprecation window and for AccessContext.from_context().
KEYCARD_STATE_KEY = "keycardai"

# Testing seam: when set, grant() skips token acquisition and exchange and
# yields this AccessContext instead. Set via override_access_context().
_access_context_override: ContextVar[AccessContext | None] = ContextVar(
    "keycardai_access_context_override", default=None
)


@contextmanager
def override_access_context(access_context: AccessContext):
    """Force Keycard grants to resolve to the given AccessContext.

    Public testing seam: while the context manager is active, every grant
    resolution (injected-parameter or decorator form) skips caller-token
    lookup and RFC 8693 exchange and produces ``access_context`` instead.
    This is the supported way to fake delegated access in tests without
    patching module internals.

    Args:
        access_context: The AccessContext instance to inject into tools.

    Example:
        ```python
        from keycardai.fastmcp import AccessContext, override_access_context
        from keycardai.oauth.types.models import TokenResponse

        access = AccessContext()
        access.set_token("https://api.example.com", TokenResponse(
            access_token="fake", token_type="Bearer",
        ))
        with override_access_context(access):
            result = await my_tool.run(...)
        ```
    """
    token = _access_context_override.set(access_context)
    try:
        yield access_context
    finally:
        _access_context_override.reset(token)


def _annotation_matches(annotation: Any, target: type) -> bool:
    """Check whether a parameter annotation refers to ``target``.

    Handles resolved classes, subclasses, ``X | None`` / Optional unions,
    ``Annotated[X, ...]``, and unresolved string annotations (as produced by
    ``from __future__ import annotations`` when get_type_hints cannot resolve
    them).
    """
    if annotation is inspect.Parameter.empty or annotation is None:
        return False
    if annotation is target:
        return True
    if inspect.isclass(annotation) and issubclass(annotation, target):
        return True
    if isinstance(annotation, str):
        # Unresolvable forward reference: match on the bare class name.
        # Reached only when get_type_hints failed for the whole function, so a
        # same-named unrelated class can false-positive here; the tradeoff is
        # accepted to keep TYPE_CHECKING-only imports working.
        parts = [part.strip() for part in annotation.split("|")]
        return any(part.split(".")[-1] == target.__name__ for part in parts)
    origin = get_origin(annotation)
    if origin is Annotated:
        return _annotation_matches(get_args(annotation)[0], target)
    if origin is Union or origin is UnionType or isinstance(annotation, UnionType):
        return any(_annotation_matches(arg, target) for arg in get_args(annotation))
    return False


def _find_param_of_type(func: Callable, target: type) -> str | None:
    """Find the first parameter of ``func`` annotated with ``target``.

    Resolves string annotations via get_type_hints so functions defined in
    modules using ``from __future__ import annotations`` are handled
    correctly.
    """
    signature = inspect.signature(func)
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:
        hints = {}
    for name, parameter in signature.parameters.items():
        annotation = hints.get(name, parameter.annotation)
        if _annotation_matches(annotation, target):
            return name
    return None


def _get_context(*args, **kwargs) -> Context | None:
    """Find a FastMCP Context instance in a call's arguments."""
    for value in args:
        if isinstance(value, Context):
            return value
    for value in kwargs.values():
        if isinstance(value, Context):
            return value
    return None


def _current_context_or_none() -> Context | None:
    """Return the active FastMCP request context, or None outside a request."""
    try:
        return get_context()
    except RuntimeError:
        return None


async def _call_func(is_async_func: bool, func: Callable, *args, **kwargs):
    if is_async_func:
        return await func(*args, **kwargs)
    return func(*args, **kwargs)


def _scope_for(
    request_scopes: str | list[str] | dict[str, str | list[str]] | None,
    resource: str,
) -> str | None:
    """Resolve the RFC 8693 scope string to request for a resource."""
    if request_scopes is None:
        return None
    value = (
        request_scopes.get(resource)
        if isinstance(request_scopes, dict)
        else request_scopes
    )
    if value is None:
        return None
    scope = " ".join(value) if isinstance(value, list) else value
    return scope or None


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
        return {"resources": self._resource_errors.copy(), "error": self._error}

    def get_error(self) -> dict[str, str] | None:
        """Get global error if any."""
        return self._error

    def get_resource_error(self, resource: str) -> dict[str, str] | None:
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
            raise ResourceAccessError(
                resource=resource,
                error_type="global_error",
                error_details=self.get_error()
            )

        # Check for resource-specific error
        if self.has_resource_error(resource):
            raise ResourceAccessError(
                resource=resource,
                error_type="resource_error",
                error_details=self.get_resource_error(resource)
            )

        # Check if token exists
        if resource not in self._access_tokens:
            raise ResourceAccessError(
                resource=resource,
                error_type="missing_token",
                available_resources=list(self._access_tokens.keys())
            )

        return self._access_tokens[resource]

    @classmethod
    async def from_context(cls, ctx: Context) -> AccessContext:
        """Read the AccessContext stored on a FastMCP Context.

        Escape hatch for helpers called from inside tools that receive the
        FastMCP ``Context`` but not the injected ``AccessContext`` parameter.
        Prefer declaring the parameter directly on the tool:
        ``access: AccessContext = auth_provider.grant(...)``.

        Args:
            ctx: The FastMCP request context.

        Returns:
            The AccessContext written by a grant for this request. If no
            grant ran, returns an AccessContext with a global error recorded
            (this method never raises).
        """
        state = await ctx.get_state(KEYCARD_STATE_KEY)
        if isinstance(state, AccessContext):
            return state
        access_context = cls()
        access_context.set_error({
            "message": (
                "No Keycard access context is available on this request. "
                "Declare an AccessContext parameter with auth_provider.grant(...) "
                "or apply the grant decorator to the tool."
            ),
        })
        return access_context


class GrantDependency(Dependency[AccessContext]):
    """Injectable dependency that performs delegated token exchange.

    Returned by :meth:`AuthProvider.grant`. One object supports both idioms:

    - **Injected parameter (preferred)**: used as a parameter default, FastMCP
      resolves it per request via ``fastmcp.dependencies`` and injects the
      populated :class:`AccessContext`. The parameter is excluded from the
      tool's input schema.

      ```python
      @mcp.tool()
      async def get_github_user(
          access: AccessContext = auth_provider.grant("https://api.github.com"),
      ) -> dict:
          token = access.access("https://api.github.com").access_token
          ...
      ```

    - **Decorator (deprecated result access via get_state)**: applied with
      ``@auth_provider.grant(...)``, the same object wraps the function. If
      the function declares an :class:`AccessContext` parameter it is
      injected there; otherwise a :class:`DeprecationWarning` is emitted at
      decoration time and the result must be read via
      ``await ctx.get_state("keycardai")``.

    Errors are recorded on the returned AccessContext, never raised
    (see :meth:`AccessContext.get_errors`). Multi-resource grants are
    all-or-nothing: if any exchange fails, the AccessContext carries the
    failing resource's error and no tokens, including tokens for resources
    that exchanged successfully before the failure.

    Instances are stateless between calls: all per-request state lives on the
    AccessContext produced by each resolution, so a single instance is safe to
    share across concurrent requests.
    """

    def __init__(
        self,
        provider: AuthProvider,
        resources: str | list[str],
        request_scopes: str | list[str] | dict[str, str | list[str]] | None = None,
    ):
        self._provider = provider
        self._resources = [resources] if isinstance(resources, str) else list(resources)
        self._request_scopes = request_scopes

    async def __aenter__(self) -> AccessContext:
        access_context = await self._provider._build_access_context(
            self._resources, self._request_scopes
        )
        # Dual-write to FastMCP context state while ctx.get_state("keycardai")
        # remains supported; also backs AccessContext.from_context().
        ctx = _current_context_or_none()
        if ctx is not None:
            await ctx.set_state(KEYCARD_STATE_KEY, access_context, serializable=False)
        return access_context

    def __call__(self, func: Callable) -> Callable:
        """Apply as a decorator, preserving the ``@auth_provider.grant(...)`` spelling.

        The decorated function must declare an :class:`AccessContext`
        parameter (injected, hidden from the tool schema) or a FastMCP
        ``Context`` parameter (deprecated: result read via
        ``ctx.get_state("keycardai")``).

        Raises:
            MissingContextError: If the function declares neither an
                AccessContext parameter nor a Context parameter, or if no
                Context can be found at call time on the deprecated path.
        """
        provider = self._provider
        resources = self._resources
        request_scopes = self._request_scopes

        access_param = _find_param_of_type(func, AccessContext)
        ctx_param = _find_param_of_type(func, Context)
        if access_param is None and ctx_param is None:
            raise MissingContextError(
                function_name=func.__name__,
                parameters=list(inspect.signature(func).parameters.keys())
            )
        if access_param is None:
            warnings.warn(
                f"Tool '{func.__name__}' uses the grant decorator without declaring "
                "an AccessContext parameter; reading the result via "
                'ctx.get_state("keycardai") is deprecated. Declare a parameter '
                "like `access: AccessContext = auth_provider.grant(...)` instead.",
                DeprecationWarning,
                stacklevel=2,
            )

        is_async_func = inspect.iscoroutinefunction(func)

        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            _ctx = _get_context(*args, **kwargs) or _current_context_or_none()
            if _ctx is None and access_param is None:
                raise MissingContextError(
                    function_name=func.__name__,
                    parameters=[type(arg).__name__ for arg in args] + list(kwargs.keys()),
                    runtime_context=True
                )

            _access_context = await provider._build_access_context(
                resources, request_scopes
            )
            if _ctx is not None:
                # Dual-write during the get_state deprecation window.
                await _ctx.set_state(KEYCARD_STATE_KEY, _access_context, serializable=False)
            if access_param is not None:
                kwargs[access_param] = _access_context

            logger.debug(f"Executing decorated function: {func.__name__}")
            return await _call_func(is_async_func, func, *args, **kwargs)

        if access_param is not None:
            # Hide the injected AccessContext parameter from the wrapper's
            # public signature so FastMCP excludes it from the tool schema
            # and never treats it as a user-supplied argument.
            signature = inspect.signature(func)
            wrapper.__signature__ = signature.replace(
                parameters=[
                    parameter
                    for name, parameter in signature.parameters.items()
                    if name != access_param
                ]
            )
            wrapper.__annotations__ = {
                name: annotation
                for name, annotation in wrapper.__annotations__.items()
                if name != access_param
            }
        return wrapper


class AuthProvider:
    """Keycard authentication provider for FastMCP.

    This provider integrates Keycard's zone-based authentication with FastMCP's
    authentication system. It provides a clean interface for configuring Keycard
    authentication and returns a RemoteAuthProvider instance for FastMCP integration.


    Example:
        ```python
        from fastmcp import FastMCP
        from keycardai.fastmcp import AuthProvider, AccessContext

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
        from keycardai.mcp.server.auth import ClientSecret

        auth_provider = AuthProvider(
            zone_id="abc1234",
            mcp_server_name="My FastMCP Service",
            mcp_base_url="http://localhost:8000/",
            application_credential=ClientSecret(("client_id", "client_secret"))
        )

        # Get the RemoteAuthProvider for FastMCP
        auth = auth_provider.get_remote_auth_provider()
        mcp = FastMCP("My Protected Service", auth=auth)

        # Declare a grant as a typed tool parameter for token exchange
        @mcp.tool()
        async def my_tool(
            user_id: str,
            access: AccessContext = auth_provider.grant("https://api.example.com"),
        ):
            # Use the injected access context to check the status of the
            # token exchange and handle the error state accordingly
            if access.has_errors():
                print("Failed to obtain access token for resource")
                print(f"Error: {access.get_errors()}")
                return
            token = access.access("https://api.example.com").access_token
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
        mcp_server_url: str | None = None,
        base_url: str | None = None,
        application_credential: ApplicationCredential | None = None,
        client_factory: ClientFactory | None = None,
        # deprecated
        mcp_base_url: str | None = None,
    ):
        """Initialize Keycard authentication provider.

        Args:
            zone_id: Keycard zone ID for OAuth operations.
            zone_url: Keycard zone URL for OAuth operations. If not provided and zone_id is given,
                     will be constructed using base_url or default keycard.cloud domain.
            mcp_server_name: Human-readable service name for metadata
            required_scopes: Required Keycard scopes for access
            mcp_base_url: Resource server URL for the FastMCP server
            base_url: Base URL for Keycard zone
            application_credential: Workload credential provider for token exchange. Use ClientSecret
                 for Keycard-issued credentials, WebIdentity for private key JWT,
                 EKSWorkloadIdentity for EKS workload identity, or None for basic token
                 exchange without client authentication.
            client_factory: Client factory for creating OAuth clients. Defaults to DefaultClientFactory

        Raises:
            AuthProviderConfigurationError: If neither zone_url nor zone_id is provided, or if custom client factory fails
            AuthProviderInternalError: If default OAuth client creation fails (internal SDK issue - contact support)
            AuthProviderRemoteError: If cannot connect to Keycard zone (check zone configuration or contact support)
        """
        # Discover configuration from environment variables with explicit parameters taking priority
        zone_id = zone_id or os.getenv("KEYCARD_ZONE_ID")
        zone_url = zone_url or os.getenv("KEYCARD_ZONE_URL")
        base_url = base_url or os.getenv("KEYCARD_BASE_URL")
        mcp_server_url = mcp_server_url or os.getenv("MCP_SERVER_URL")

        if zone_url is None and zone_id is None:
            raise AuthProviderConfigurationError(zone_url=zone_url, zone_id=zone_id)

        self.zone_url = self._build_zone_url(zone_url, zone_id, base_url)
        self.mcp_server_name = mcp_server_name or "Authenticated FastMCP Server"
        self.required_scopes = required_scopes or []

        if mcp_server_url is None:
            if mcp_base_url is None:
                raise AuthProviderConfigurationError(mcp_server_url=mcp_server_url, missing_mcp_server_url=True)
            mcp_server_url = mcp_base_url
        self.mcp_server_url = mcp_server_url
        parsed_url = urlparse(self.mcp_server_url)
        # Appends `/` to any URL. Required to ensure audience is properly aligned with FastMCP JWTVerifier which appends `/` to the audience.
        self.mcp_base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
        # fastmcp automatically appends `/mcp` to the base_url when presenting Protected Resource to the clients.
        # we need to append `/mcp` to the mcp_base_url to ensure the audience is properly aligned with FastMCP JWTVerifier.
        # Also accept zone_url as a valid audience: Keycard PKCE access tokens carry aud=zone_url
        # (the resource is in a separate `resource` claim), so we must accept both forms.
        self.audience = [f"{self.mcp_base_url}mcp", self.zone_url]
        self.client_name = self.mcp_server_name or "Keycard Auth Client"

        self.client_factory = client_factory or DefaultClientFactory()
        self._is_custom_factory = client_factory is not None

        # Initialize application credential provider
        self.application_credential = self._discover_application_credential(application_credential)

        # Get the auth strategy for the HTTP client doing the token exchange
        if self.application_credential is not None:
            self.auth = self.application_credential.get_http_client_auth()
        else:
            self.auth = NoneAuth()

        try:
            self.client: AsyncClient | None = self.client_factory.create_async_client(self.zone_url, auth=self.auth)
        except Exception as e:
            self._handle_client_creation_error(self.auth, e)

        if self.client is None:
            self._handle_client_creation_error(self.auth)

        try:
            self.jwks_uri = self._discover_jwks_uri(self.client_factory.create_client(self.zone_url))
        except Exception as e:
            raise AuthProviderRemoteError(
                zone_url=self.zone_url,
            ) from e

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

    def _handle_client_creation_error(self, auth, exception: Exception | None = None) -> None:
        """Handle client creation errors with appropriate exception type.

        Args:
            auth: Authentication strategy being used
            exception: Original exception if client creation threw an exception
        """
        if self._is_custom_factory:
            # Custom factory failure - this is a configuration issue
            error_kwargs = {
                "zone_url": self.zone_url,
                "factory_type": type(self.client_factory).__name__
            }
            if exception:
                raise AuthProviderConfigurationError(**error_kwargs) from exception
            else:
                raise AuthProviderConfigurationError(**error_kwargs)
        else:
            # Default factory should never fail due to lazy initialization
            # This would indicate a serious internal issue
            error_kwargs = {
                "zone_url": self.zone_url,
                "auth_type": type(auth).__name__ if auth else "NoneAuth",
                "component": "default_client_factory"
            }
            if exception:
                raise AuthProviderInternalError(**error_kwargs) from exception
            else:
                raise AuthProviderInternalError(**error_kwargs)

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

    def _discover_jwks_uri(self, client: Client) -> str | None:
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

        Creates a JWTVerifier configured with the zone's JWKS URI and issuer
        information that was discovered during AuthProvider initialization.

        Note: Zone metadata discovery happens in __init__. This method only
        creates the verifier object with already-discovered values.

        Returns:
            JWTVerifier: Configured JWT token verifier for the Keycard zone
        """
        return JWTVerifier(
            jwks_uri=self.jwks_uri,
            issuer=self.zone_url,
            required_scopes=self.required_scopes,
            audience=self.audience,
        )

    def get_remote_auth_provider(self) -> RemoteAuthProvider:
        """Get a RemoteAuthProvider instance configured for Keycard authentication.

        Creates a RemoteAuthProvider using the zone configuration that was
        discovered and validated during AuthProvider initialization.

        Note: Zone metadata discovery and validation happens in __init__. This method
        only creates the RemoteAuthProvider object with already-validated configuration.

        Returns:
            RemoteAuthProvider: Configured authentication provider for use with FastMCP
        """

        authorization_servers = [AnyHttpUrl(self.zone_url)]

        return RemoteAuthProvider(
            token_verifier=self.get_jwt_token_verifier(),
            authorization_servers=authorization_servers,
            base_url=self.mcp_base_url,
            resource_name=self.mcp_server_name,
        )

    def grant(
        self,
        resources: str | list[str],
        *,
        request_scopes: str | list[str] | dict[str, str | list[str]] | None = None,
    ) -> GrantDependency:
        """Delegated token exchange for one or more resources.

        Returns a :class:`GrantDependency` that automates the OAuth token
        exchange process (RFC 8693) for accessing external resources on behalf
        of authenticated users. Use it as a typed parameter default (preferred)
        or as a decorator (the parameter-less get_state access is deprecated).

        The injected value is an instance of AccessContext, which can be used
        to check the status of the token exchange.

        Grant resolution avoids raising exceptions, and instead sets the error
        state in the AccessContext.

        Args:
            resources: Target resource URL(s) for token exchange.
                      Can be a single string or list of strings.
                      (e.g., "https://api.example.com" or
                       ["https://api.example.com", "https://other-api.com"])
            request_scopes: Optional OAuth scope(s) to request during the token
                      exchange (RFC 8693 ``scope`` parameter), forwarded to Keycard
                      so scope-gated delegation policies can match. Accepts:
                      - ``str``: a single (space-delimited) scope string applied to
                        every resource (e.g. ``"read"``).
                      - ``list[str]``: joined with spaces and applied to every
                        resource.
                      - ``dict[str, str | list[str]]``: per-resource scopes keyed by
                        resource URL; resources absent from the dict request no scope.
                      Defaults to ``None`` (no scope sent).

                      Note: ``request_scopes`` is the *outbound* scope requested
                      during exchange. It is distinct from ``required_scopes`` on
                      ``AuthProvider``, which the ``JWTVerifier`` enforces on the
                      *inbound* caller token.

        Usage:
            ```python
            from fastmcp import FastMCP
            from keycardai.fastmcp import AuthProvider, AccessContext

            auth_provider = AuthProvider(zone_id="abc1234", mcp_base_url="http://localhost:8000")
            auth = auth_provider.get_remote_auth_provider()
            mcp = FastMCP("Server", auth=auth)

            @mcp.tool()
            async def my_tool(
                user_id: str,
                access: AccessContext = auth_provider.grant("https://api.example.com"),
            ):
                if access.has_errors():
                    print("Failed to obtain access token for resource")
                    print(f"Error: {access.get_errors()}")
                    return
                token = access.access("https://api.example.com").access_token
                headers = {"Authorization": f"Bearer {token}"}
                # Use headers to call external API
                return f"Data for {user_id}"

            # Request a scope for a single resource
            @mcp.tool()
            async def scoped_tool(
                access: AccessContext = auth_provider.grant(
                    "https://api.example.com",
                    request_scopes="read",
                ),
            ):
                ...

            # Per-resource scopes when exchanging for multiple resources
            @mcp.tool()
            async def multi_tool(
                access: AccessContext = auth_provider.grant(
                    ["https://api1.example.com", "https://api2.example.com"],
                    request_scopes={
                        "https://api1.example.com": "read",
                        "https://api2.example.com": ["read", "write"],
                    },
                ),
            ):
                ...
            ```

        The decorator form remains supported from the same object:
        ``@auth_provider.grant(...)`` above the function. On that path the
        function must declare an AccessContext parameter (injected, hidden
        from the tool schema) or a FastMCP Context parameter; without an
        AccessContext parameter a DeprecationWarning is emitted and the
        result must be read via ``await ctx.get_state("keycardai")``.

        Raises:
            MissingContextError: Decorator form only: if the decorated function
                                declares neither an AccessContext parameter nor a
                                Context parameter, or if Context cannot be found
                                at call time on the deprecated get_state path.

        Error handling:
        - Records structured errors on the AccessContext if token exchange fails
        - Preserves original function signature and behavior
        - Provides detailed error messages for debugging
        """
        return GrantDependency(self, resources, request_scopes)

    async def _build_access_context(
        self,
        resources: list[str],
        request_scopes: str | list[str] | dict[str, str | list[str]] | None = None,
    ) -> AccessContext:
        """Acquire the caller token and exchange it for each resource.

        Never raises: failures are recorded on the returned AccessContext as
        a global error (token acquisition) or a resource error (exchange).
        Multi-resource grants are all-or-nothing: the first failed exchange
        stops the loop and no tokens are populated, including ones already
        exchanged successfully.
        Honors the override_access_context() testing seam.
        """
        override = _access_context_override.get()
        if override is not None:
            return override

        logger.debug(f"Starting token exchange for resources: {resources}")
        _access_context = AccessContext()
        try:
            _user_token = get_access_token()
            if not _user_token:
                logger.warning("No authentication token available")
                _access_context.set_error({
                    "message": "No authentication token available. Please ensure you're properly authenticated.",
                })
                return _access_context
            logger.introspect(f"User token retrieved: {get_token_debug_info(_user_token.token)}")
        except Exception as e:
            logger.error("Failed to get access token")
            _access_context.set_error({
                "message": "Failed to get access token from the request context. Please ensure you're properly authenticated.",
                "raw_error": str(e),
            })
            return _access_context

        _access_tokens = {}
        for resource in resources:
            logger.debug(f"Exchanging token for resource: {resource}")
            try:
                if self.application_credential:
                    logger.debug(f"Using application credential: {type(self.application_credential).__name__}")
                    # auth_info context is used by application credential implementation
                    # to prepare correct assertions in the token exchange request.
                    # For WebIdentity, use the stable WIF key_id so the client assertion
                    # JWT has a predictable `iss` that can be pre-registered in Keycard.
                    # Falling back to the DCR client_id would produce an ephemeral `ua:...`
                    # identifier that changes on every restart and cannot be pre-registered.
                    _resource_client_id = (
                        self.application_credential.identity_manager.key_id
                        if hasattr(self.application_credential, "identity_manager")
                        else self.client.config.client_id or ""
                    )
                    _auth_info = {
                        "resource_client_id": _resource_client_id,
                        "resource_server_url": self.mcp_base_url,
                        "zone_id": "",
                    }
                    _token_exchange_request = await self.application_credential.prepare_token_exchange_request(
                        client=self.client,
                        subject_token=_user_token.token,
                        resource=resource,
                        auth_info=_auth_info,
                    )
                else:
                    _token_exchange_request = TokenExchangeRequest(
                        subject_token=_user_token.token,
                        resource=resource,
                        subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                    )

                _scope = _scope_for(request_scopes, resource)
                if _scope:
                    _token_exchange_request.scope = _scope

                _token_response = await self.client.exchange_token(_token_exchange_request)

                _access_tokens[resource] = _token_response
                logger.debug(f"Token exchange successful for {resource}")
                logger.introspect(f"Token details for {resource}: {get_token_debug_info(_token_response.access_token)}")
            except Exception as e:
                logger.error(f"Token exchange failed for {resource}")
                _error_dict: dict[str, str] = {
                    "message": f"Token exchange failed for {resource}",
                }
                if hasattr(e, "error"):
                    _error_dict["code"] = e.error
                if hasattr(e, "error_description") and e.error_description:
                    _error_dict["description"] = e.error_description
                if not hasattr(e, "error"):
                    _error_dict["raw_error"] = str(e)
                _access_context.set_resource_error(resource, _error_dict)
                return _access_context

        logger.debug(f"All token exchanges completed. Populating access context with {len(_access_tokens)} token(s)")
        _access_context.set_bulk_tokens(_access_tokens)
        return _access_context
