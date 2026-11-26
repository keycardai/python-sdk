"""OAuth 2.0 client implementation"""

import asyncio
import concurrent.futures
import os
import sys
import threading
import warnings
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, TypeVar, overload

from ._metadata_cache import MetadataCache
from .client_auth import (
    ClientAuthentication,
    ClientAuthenticator,
    require_client_authenticator,
)
from .clock import Clock, SystemClock
from .exceptions import (
    AuthorizationServerDiscoveryError,
    ConfigError,
    DeviceAuthorizationError,
    DevicePollError,
    LegacyGrantCredentialWarning,
    MissingTokenEndpointError,
    NetworkError,
    OAuthHttpError,
    ResponseBodyTooLargeError,
    TokenRevocationError,
    _is_transient_status,
)
from .http._context import build_http_context
from .http._transports import (
    HttpxAsyncTransport,
    HttpxTransport,
    ResponseBodyDecodeError,
)
from .http._wire import HttpRequest, HttpResponse
from .http.auth import (
    AuthStrategy,
    BasicAuth,
    NoneAuth,
)
from .http.transport import AsyncHTTPTransport, HTTPTransport
from .operations._authorize import (
    exchange_authorization_code as _exchange_authorization_code,
    exchange_authorization_code_async as _exchange_authorization_code_async,
    refresh_token_grant as _refresh_token_grant,
    refresh_token_grant_async as _refresh_token_grant_async,
)
from .operations._client_credentials import (
    client_credentials_grant,
    client_credentials_grant_async,
)
from .operations._device_authorization import (
    DEVICE_AUTHORIZATION_OPERATION,
    DEVICE_TOKEN_OPERATION,
    SLOW_DOWN_INCREMENT,
    build_device_authorization_http_request,
    build_device_token_http_request,
    check_endpoint,
    parse_device_authorization_http_response,
    parse_device_token_http_response,
    undecodable_body_error,
)
from .operations._discovery import (
    discover_server_metadata,
    discover_server_metadata_async,
)
from .operations._introspection import (
    INTROSPECTION_OPERATION,
    build_introspection_http_request,
    check_introspection_auth,
    invalid_introspection_response,
    parse_introspection_http_response,
)
from .operations._registration import (
    register_client,
    register_client_async,
)
from .operations._revocation import (
    REVOCATION_OPERATION,
    build_revocation_http_request,
    parse_revocation_http_response,
)
from .operations._token_exchange import (
    exchange_token,
    exchange_token_async,
    resolve_token_exchange_auth_headers,
)
from .operations._userinfo import (
    fetch_userinfo,
    fetch_userinfo_async,
    resolve_userinfo_endpoint,
)
from .types.models import (
    AuthorizationServerMetadata,
    ClientConfig,
    ClientCredentialsRequest,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    DeviceAuthorization,
    Endpoints,
    ResolvedEndpoints,
    ServerMetadataRequest,
    TokenExchangeRequest,
    TokenIntrospection,
    TokenResponse,
    UserInfoRequest,
    UserInfoResponse,
)
from .types.oauth import (
    GrantType,
    OAuth2DefaultEndpoints,
    ResponseType,
    TokenEndpointAuthMethod,
    TokenType,
    TokenTypeHint,
)
from .utils.jwt import build_substitute_user_token

if TYPE_CHECKING:
    from .server.credentials import (
        ApplicationCredential,
        IdentityTokenSource,
        _IdentityTokenFetcher,
    )


_exchange_loop: asyncio.AbstractEventLoop | None = None
_exchange_loop_lock = threading.Lock()
_T = TypeVar("_T")
_stateless_grant = ContextVar("_stateless_grant", default=False)


@contextmanager
def _stateless_grant_call() -> Iterator[None]:
    """Mark per-call client authentication from a stateless helper.

    Stateless helpers take the credential per call by design, so the client
    methods they call do not emit LegacyGrantCredentialWarning.
    """
    token = _stateless_grant.set(True)
    try:
        yield
    finally:
        _stateless_grant.reset(token)


def _warn_per_call_authentication(
    method: str, credential: object | None, client_id: str | None
) -> None:
    if _stateless_grant.get() or (credential is None and client_id is None):
        return
    name = "credential" if credential is not None else "client_id"
    warnings.warn(
        f"Passing {name} to {method} is deprecated; configure {name} when "
        "constructing the client.",
        LegacyGrantCredentialWarning,
        stacklevel=3,
    )


def _reset_exchange_loop_after_fork() -> None:
    global _exchange_loop, _exchange_loop_lock
    _exchange_loop = None
    _exchange_loop_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_exchange_loop_after_fork)


async def _prepare_exchange_assertion(
    credential: "ApplicationCredential",
    request: TokenExchangeRequest,
    client: "Client | AsyncClient",
    issuer: str,
    endpoints: ResolvedEndpoints,
) -> TokenExchangeRequest:
    subject_token = request.subject_token
    if subject_token is None:
        raise ConfigError("subject_token is required to prepare token exchange")
    if (
        isinstance(client, AsyncClient)
        and issuer == client.issuer
        and endpoints == await client.get_endpoints()
    ):
        prepared = await credential.prepare_token_exchange_request(
            client, subject_token, request.resource or ""
        )
    else:
        async with AsyncClient(
            issuer,
            endpoints=endpoints,
            auth=client.auth_strategy,
            transport=client.transport if isinstance(client, AsyncClient) else None,
            config=replace(
                client.config, enable_metadata_discovery=False, auto_register_client=False
            ),
        ) as preparation_client:
            prepared = await credential.prepare_token_exchange_request(
                preparation_client, subject_token, request.resource or ""
            )
    return request.model_copy(
        update={
            "client_assertion": prepared.client_assertion,
            "client_assertion_type": prepared.client_assertion_type,
            "client_id": prepared.client_id,
        }
    )


def _prepare_exchange_assertion_sync(
    credential: "ApplicationCredential",
    request: TokenExchangeRequest,
    client: "Client",
    issuer: str,
    endpoints: ResolvedEndpoints,
    timeout: float,
) -> TokenExchangeRequest:
    return _run_credential_operation(
        _prepare_exchange_assertion(credential, request, client, issuer, endpoints),
        timeout,
        "token exchange assertion preparation",
    )


def _run_credential_operation(
    operation: Coroutine[Any, Any, _T],
    timeout: float,
    step: str,
    *,
    error_operation: str | None = None,
    timeout_error: Callable[[TimeoutError], Exception] | None = None,
) -> _T:
    """Keep source- and credential-owned connections on one persistent loop."""
    global _exchange_loop
    with _exchange_loop_lock:
        loop = _exchange_loop
        if loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever,
                name="keycard-token-exchange",
                daemon=True,
            ).start()
            _exchange_loop = loop
    future = asyncio.run_coroutine_threadsafe(operation, loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as e:
        future.cancel()
        cause = TimeoutError(f"{step} exceeded {timeout} seconds")
        if timeout_error is not None:
            raise timeout_error(cause) from cause
        raise NetworkError(cause, operation=error_operation or step) from e


def _run_exchange_coroutine(
    operation: Coroutine[Any, Any, _T],
    timeout: float,
    step: str,
    *,
    timeout_error: Callable[[TimeoutError], Exception] | None = None,
) -> _T:
    return _run_credential_operation(
        operation,
        timeout,
        step,
        error_operation=f"token exchange {step}",
        timeout_error=timeout_error,
    )


def _actor_fetcher(
    actor: "IdentityTokenSource | Callable[[], Awaitable[str] | str] | None",
) -> "_IdentityTokenFetcher | None":
    if actor is None:
        return None
    from .server.credentials import _IdentityTokenFetcher

    return _IdentityTokenFetcher(actor)


def _add_exchange_actor(request: TokenExchangeRequest, token: str) -> TokenExchangeRequest:
    return request.model_copy(
        update={
            "actor_token": token,
            "actor_token_type": TokenType.JWT,
        }
    )


def _actor_lookup_timeout(cause: TimeoutError) -> Exception:
    from .server.credentials import WORKLOAD_IDENTITY_SOURCE_CUSTOM
    from .server.exceptions import WorkloadIdentityRuntimeError

    return WorkloadIdentityRuntimeError(
        str(cause), source=WORKLOAD_IDENTITY_SOURCE_CUSTOM, retryable=True
    )


class _PublicClient:
    """Client authentication for a public client: ``client_id`` in the body."""

    def __init__(self, client_id: str):
        self.client_id = client_id

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        return ClientAuthentication(form_fields={"client_id": self.client_id})


class _StaticClientAuthentication:
    """Client authentication with fixed headers, such as HTTP Basic."""

    def __init__(self, headers: dict[str, str]):
        self._headers = headers

    async def client_authentication(
        self, *, issuer: str, endpoint: str
    ) -> ClientAuthentication:
        return ClientAuthentication(headers=dict(self._headers))


async def _run_with_timeout(coro: Coroutine[Any, Any, _T], limit: float) -> _T:
    """Run ``coro`` for at most ``limit`` seconds.

    Running out of time raises asyncio.TimeoutError. Cancelling the caller
    always raises CancelledError, also when ``coro`` finishes at the same
    moment. asyncio.wait_for on Python 3.10 and 3.11 can return the result
    instead (CPython gh-86296), so it is not used here.
    """
    if sys.version_info >= (3, 11):
        async with asyncio.timeout(limit):
            return await coro
    task = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({task}, timeout=limit)
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.wait({task})
        if not task.cancelled():
            # Mark the exception as retrieved. The cancellation wins.
            task.exception()
        raise
    if task in done:
        return task.result()
    task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        task.exception()
    raise asyncio.TimeoutError


@dataclass(frozen=True)
class _ClientAuthenticationFailure:
    """An exception the credential's client authentication raised.

    The device grant helpers return it instead of raising it. Callers raise
    ``error`` unchanged, outside any handler for request failures, so a
    credential failure never becomes a DevicePollError.
    """

    error: Exception


def _device_client_authenticator(
    credential: object | None,
    client_id: str | None,
    configured: object | None,
    configured_client_id: str | None,
) -> ClientAuthenticator:
    """Return the client authentication for one device authorization flow.

    Per-call ``credential`` or ``client_id`` replaces the configured
    authentication.

    Raises:
        ConfigError: Both per-call inputs are set, the client has no
            authentication, or the credential does not provide client
            authentication.
    """
    if credential is None and client_id is None:
        credential, client_id = configured, configured_client_id
    if credential is not None and client_id is not None:
        raise ConfigError("Pass exactly one of `credential` and `client_id`.")
    if credential is None and client_id is None:
        raise ConfigError(
            "Configure `credential` or a public `client_id` on the client."
        )
    if credential is not None:
        return require_client_authenticator(credential)
    if not client_id:
        raise ConfigError("`client_id` is empty.")
    return _PublicClient(client_id)


def _client_repr(client: "AsyncClient | Client") -> str:
    client_id = client._client_id or client._public_client_id or client.config.client_id
    if not client_id:
        auth_client_id = getattr(client.auth_strategy, "client_id", None)
        if isinstance(auth_client_id, str):
            client_id = auth_client_id
    return f"{type(client).__name__}(issuer={client.issuer!r}, client_id={client_id!r})"


def _grant_authenticator(
    configured: object | None,
    credential: object | None = None,
    client_id: str | None = None,
    *,
    interactive: bool = True,
) -> ClientAuthenticator | None:
    """Return the client authentication for one grant request.

    The client credentials grant is not interactive, so a WorkloadIdentity
    without ``client_id`` sends its assertion alone, as for token exchange.
    """
    if client_id is not None:
        if credential is not None:
            raise ConfigError("Pass either `credential` or `client_id`, not both.")
        return None
    selected = credential if credential is not None else configured
    if selected is None:
        return None
    if not interactive:
        from .server.credentials import WorkloadIdentity

        if isinstance(selected, WorkloadIdentity):
            return selected
    return require_client_authenticator(selected)


def _resolve_grant_credential(
    credential: "ApplicationCredential | None",
    client_id: str | None,
    client_secret: str | None,
    auth: AuthStrategy | None,
) -> "tuple[ApplicationCredential | None, str | None]":
    """Return the configured credential and public client ID.

    Raises:
        ConfigError: ``credential`` is combined with ``auth`` or with a raw
            ``client_id``, ``client_secret`` is set without ``client_id``, or
            ``client_id`` is empty.
    """
    if credential is not None and auth is not None:
        raise ConfigError("Pass either `credential` or `auth`, not both.")
    if client_id is None and client_secret is None:
        return credential, None
    if credential is not None or auth is not None:
        raise ConfigError(
            "Client ID and secret cannot be combined with credential or auth."
        )
    if not client_id:
        raise ConfigError("Pass a nonempty client ID.")
    if client_secret is None:
        return None, client_id
    if not client_secret:
        raise ConfigError("Pass a nonempty client secret.")
    from .server.credentials import ClientSecret

    return ClientSecret((client_id, client_secret)), None


def _resolve_issuer_arg(issuer: str | None, base_url: str | None) -> str:
    """Resolve the canonical issuer from the ``issuer`` / deprecated ``base_url`` args.

    Emits a ``DeprecationWarning`` when ``base_url`` is supplied. Raises
    ``ConfigError`` if both are supplied or neither resolves to a value.
    """
    if base_url is not None:
        warnings.warn(
            "`base_url` is deprecated; use `issuer` instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        if issuer is not None:
            raise ConfigError("Pass either `issuer` or `base_url`, not both.")
        issuer = base_url
    if not issuer:
        raise ConfigError("issuer is required")
    return issuer.rstrip("/")


def _resolve_discovery_issuer(args: dict[str, Any], default_issuer: str) -> str:
    """Resolve the issuer for a discovery call from its keyword args.

    Accepts ``issuer`` (canonical) or ``base_url`` (deprecated), falling back to
    the client's own issuer. Emits a ``DeprecationWarning`` for ``base_url``.
    """
    if "issuer" in args and "base_url" in args:
        raise ConfigError("Pass either `issuer` or `base_url`, not both.")
    if "base_url" in args:
        warnings.warn(
            "`base_url` is deprecated; use `issuer` instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        return args["base_url"]
    return args.get("issuer", default_issuer)


def _cached_metadata_for_endpoint(
    cache: MetadataCache[AuthorizationServerMetadata, AuthorizationServerDiscoveryError],
    endpoint_name: str | None,
    *,
    overridden: bool,
    refresh: bool,
) -> AuthorizationServerMetadata | None:
    metadata = cache.lookup(refresh=refresh)
    if (
        metadata is not None
        and endpoint_name is not None
        and not overridden
        and not getattr(metadata, endpoint_name)
    ):
        age = cache.success_age()
        if age is not None and age >= cache.negative_ttl:
            return cache.lookup(refresh=True)
    return metadata


def _check_required_token_endpoint(
    issuer: str,
    metadata: AuthorizationServerMetadata,
    endpoint_name: str | None,
    overridden: bool,
) -> AuthorizationServerMetadata:
    if endpoint_name == "token_endpoint" and not metadata.token_endpoint and not overridden:
        raise MissingTokenEndpointError(issuer)
    return metadata


def _introspection_authenticator(
    token: str | None,
    credential: object | None,
) -> ClientAuthenticator:
    if not token:
        raise ConfigError("`token` is required for token introspection.")
    if credential is None:
        raise ConfigError("A confidential client credential is required for introspection.")
    return require_client_authenticator(credential)


def _introspection_endpoint(
    issuer: str, metadata: AuthorizationServerMetadata | None
) -> str:
    if metadata is not None and metadata.issuer.rstrip("/") != issuer.rstrip("/"):
        raise ConfigError(
            f"issuer {issuer} does not match the metadata issuer {metadata.issuer}"
        )
    return check_endpoint(
        issuer,
        "introspection_endpoint",
        metadata.introspection_endpoint if metadata is not None else None,
        require_https=True,
    )


def _introspection_request(
    config: ClientConfig,
    endpoint: str,
    token: str,
    token_type_hint: str | TokenTypeHint | None,
    client_auth: ClientAuthentication,
) -> HttpRequest:
    request = build_introspection_http_request(
        endpoint=endpoint,
        token=token,
        token_type_hint=token_type_hint,
        client_auth=client_auth,
    )
    reserved = {"authorization", *(name.lower() for name in request.headers)}
    return replace(
        request,
        headers={
            "User-Agent": config.user_agent,
            **{
                name: value
                for name, value in (config.custom_headers or {}).items()
                if name.lower() not in reserved
            },
            **request.headers,
        },
    )


def resolve_endpoints(
    issuer: str,
    endpoint_overrides: Endpoints | None = None,
    discovered_metadata: "AuthorizationServerMetadata | None" = None,
) -> ResolvedEndpoints:
    """Resolve final endpoint URLs with priority: overrides > discovered > defaults.

    Args:
        issuer: Issuer URL for the OAuth 2.0 authorization server
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

    if not endpoints.token:
        endpoints.token = OAuth2DefaultEndpoints.construct_url(issuer, OAuth2DefaultEndpoints.TOKEN)
    if not endpoints.register:
        endpoints.register = OAuth2DefaultEndpoints.construct_url(issuer, OAuth2DefaultEndpoints.REGISTRATION)
    if not endpoints.authorize:
        endpoints.authorize = OAuth2DefaultEndpoints.construct_url(issuer, OAuth2DefaultEndpoints.AUTHORIZATION)
    if not endpoints.par:
        endpoints.par = OAuth2DefaultEndpoints.construct_url(issuer, OAuth2DefaultEndpoints.PUSHED_AUTHORIZATION)

    return ResolvedEndpoints(
        token=endpoints.token,
        introspect=endpoints.introspect,
        revoke=endpoints.revoke,
        register=endpoints.register,
        par=endpoints.par,
        authorize=endpoints.authorize,
    )


def create_endpoints_summary(
    endpoints: Endpoints,
    endpoint_overrides: Endpoints | None = None,
    discovered_metadata: "AuthorizationServerMetadata | None" = None,
) -> dict[str, dict[str, str]]:
    """Create diagnostic summary of resolved endpoints showing their sources.

    Args:
        endpoints: Resolved endpoints configuration
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
    """Asynchronous OAuth 2.0 client.

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
            response = await client.token_exchange(request)

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
            tokens = await client.token_exchange(request)

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
        issuer: str | None = None,
        *,
        base_url: str | None = None,
        auth: AuthStrategy | None = None,
        credential: "ApplicationCredential | None" = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        actor: "IdentityTokenSource | Callable[[], Awaitable[str] | str] | None" = None,
        endpoints: Endpoints | None = None,
        transport: AsyncHTTPTransport | None = None,
        config: ClientConfig | None = None,
        clock: Clock | None = None,
    ):
        """Initialize asynchronous OAuth 2.0 client.

        Args:
            issuer: Issuer URL of the OAuth 2.0 authorization server (RFC 8414).
            base_url: Deprecated alias for ``issuer``.
            auth: Authentication strategy (BasicAuth, BearerAuth, NoneAuth)
            credential: Application credential for grant operations. Cannot
                be combined with ``auth``.
            client_id: Client ID. With ``client_secret``, shorthand for a
                ClientSecret credential. Alone, the public client ID for
                device authorization, code exchange and refresh.
            client_secret: Client secret for shared-secret credential shorthand.
            actor: Identity token source or callable for delegation. It supplies
                a fresh actor token per exchange unless the request sets one.
                Impersonation does not use it. A sync callable must not block.
            endpoints: Endpoint overrides for multi-server deployments
            transport: Custom asynchronous HTTP transport
            config: Client configuration with timeouts, retries, etc.
            clock: Gives the current time and waits. The device
                authorization grant computes ``expires_at`` from
                ``clock.now()`` and waits between polls with
                ``clock.sleep()``. Defaults to SystemClock.
        """
        self.issuer = _resolve_issuer_arg(issuer, base_url)
        self._introspection_issuer = issuer or base_url or self.issuer
        self.config = config or ClientConfig()
        self.clock: Clock = clock or SystemClock()

        self.auth_strategy = auth or NoneAuth()
        credential, self._public_client_id = _resolve_grant_credential(
            credential, client_id, client_secret, auth
        )
        self._credential = credential
        self._exchange_actor = _actor_fetcher(actor)
        self._exchange_auth = (
            credential.get_http_client_auth()
            if auth is None and credential is not None
            else self.auth_strategy
        )

        if transport is not None:
            self.transport = transport
            self._owns_transport = False
        else:
            self.transport = HttpxAsyncTransport(
                config=self.config,
            )
            self._owns_transport = True

        self._endpoint_overrides = endpoints

        self._endpoints = resolve_endpoints(self.issuer, endpoints)

        self._initialized = False
        self._init_lock: asyncio.Lock | None = None

        self._client_id = None
        self._client_secret = None
        self._discovered_endpoints: ResolvedEndpoints | None = None
        self._discovered_metadata: AuthorizationServerMetadata | None = None
        self._metadata_cache: MetadataCache[
            AuthorizationServerMetadata, AuthorizationServerDiscoveryError
        ] = MetadataCache(self.config.discovery_ttl, self.config.negative_ttl)
        self._discovery_task: asyncio.Task[AuthorizationServerMetadata] | None = None

    def __repr__(self) -> str:
        return _client_repr(self)

    @property
    def base_url(self) -> str:
        """Deprecated alias for :attr:`issuer`. Use ``issuer`` instead."""
        return self.issuer

    async def _resolve_metadata(
        self, *, refresh: bool = False, endpoint_name: str | None = "token_endpoint"
    ) -> AuthorizationServerMetadata | None:
        """Return discovered metadata, or None when discovery is disabled.

        Success is cached for ``discovery_ttl``; a deterministic failure is
        remembered for at most ``negative_ttl``; transient failures can pace
        new fetches. Concurrent cold-cache callers share one in-flight discovery
        task that no single caller's cancellation can cancel.

        ``refresh`` discovers even when the cache is fresh. A transient
        failure leaves the cached metadata in place.
        Pass ``endpoint_name=None`` when no specific endpoint is needed.

        Raises:
            AuthorizationServerDiscoveryError: discovery failed. Never falls
                back to a guessed endpoint.
            MissingTokenEndpointError: a required token endpoint is missing.
        """
        if not self.config.enable_metadata_discovery:
            return None

        overridden = endpoint_name == "token_endpoint" and bool(
            self._endpoint_overrides and self._endpoint_overrides.token
        )
        metadata = _cached_metadata_for_endpoint(
            self._metadata_cache, endpoint_name, overridden=overridden, refresh=refresh
        )
        if metadata is None:
            task = self._discovery_task
            if task is None:
                task = asyncio.ensure_future(self._discover_and_store())
                self._discovery_task = task
                task.add_done_callback(self._on_discovery_done)
            metadata = await asyncio.shield(task)
        return _check_required_token_endpoint(
            self.issuer, metadata, endpoint_name, overridden
        )

    def _on_discovery_done(self, task: asyncio.Task[AuthorizationServerMetadata]) -> None:
        if self._discovery_task is task:
            self._discovery_task = None
        if not task.cancelled():
            task.exception()

    async def _discover_and_store(self) -> AuthorizationServerMetadata:
        try:
            metadata = await self.discover_server_metadata()
        except Exception as e:
            raise self._store_discovery_failure(
                AuthorizationServerDiscoveryError(self.issuer, cause=e)
            ) from e
        return self._store_discovered_metadata(metadata)

    def _store_discovery_failure(
        self, error: AuthorizationServerDiscoveryError
    ) -> AuthorizationServerDiscoveryError:
        return self._metadata_cache.store_failure(
            error, retryable=error.retryable, cause=error.cause
        )

    def _store_discovered_metadata(
        self, metadata: AuthorizationServerMetadata
    ) -> AuthorizationServerMetadata:
        overrides_token = self._endpoint_overrides and self._endpoint_overrides.token
        self._metadata_cache.store_success(metadata)
        self._discovered_metadata = metadata
        if metadata.token_endpoint or overrides_token:
            self._discovered_endpoints = resolve_endpoints(
                self.issuer, self._endpoint_overrides, metadata
            )
        return metadata

    async def _ensure_initialized(self) -> None:
        """Ensure client is fully initialized with discovery and registration.

        This method performs lazy initialization in an async-safe manner:
        1. Endpoint discovery (if enabled)
        2. Client registration (if auto_register_client is True)

        Uses async lock for concurrent operation safety.

        Raises:
            AuthorizationServerDiscoveryError: metadata discovery failed. The
                client is left uninitialized and the next call discovers again
                (a deterministic failure is remembered for ``negative_ttl``).
        """
        if self._initialized:
            return

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            # Double-check: another coroutine might have initialized while we waited
            if self._initialized:
                return

            endpoint_name = "token_endpoint" if self.config.auto_register_client else None
            metadata = await self._resolve_metadata(endpoint_name=endpoint_name)
            endpoints = resolve_endpoints(self.issuer, self._endpoint_overrides, metadata)
            self._discovered_endpoints = endpoints

            if self.config.auto_register_client:
                ctx = build_http_context(
                    endpoint=endpoints.register,
                    transport=self.transport,
                    auth=self.auth_strategy,
                    issuer=self.issuer,
                    user_agent=self.config.user_agent,
                    custom_headers=self.config.custom_headers,
                    timeout=self.config.timeout,
                )
                client_registration_response = await register_client_async(
                    ClientRegistrationRequest(
                        client_id=self.config.client_id,
                        client_name=self.config.client_name,
                        redirect_uris=self.config.client_redirect_uris,
                        grant_types=self.config.client_grant_types,
                        token_endpoint_auth_method=self.config.client_token_endpoint_auth_method,
                        jwks_uri=self.config.client_jwks_url,
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

        Closes the transport this client owns (see :meth:`aclose`).

        Args:
            exc_type: Exception type (if any)
            exc_value: Exception value (if any)
            traceback: Exception traceback (if any)
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Close the pooled HTTP connections held by this client's transport.

        Only a transport the client created itself is closed; an injected
        transport belongs to its owner. Closing is optional: an unclosed
        client releases its connections when it is garbage collected. The
        client stays usable after ``aclose()``; the next request opens a
        fresh connection pool.
        """
        if self._owns_transport and isinstance(self.transport, HttpxAsyncTransport):
            await self.transport.aclose()

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

    async def get_endpoints(self) -> ResolvedEndpoints:
        """Get the resolved endpoint URLs.

        Returns endpoints resolved during initialization, incorporating
        any discovered metadata and explicit overrides.

        Returns:
            Resolved Endpoints configuration.

        Raises:
            RuntimeError: If called outside of async context manager.
            MissingTokenEndpointError: If discovery supplies no token endpoint
                and none is configured.
        """
        if not self._initialized:
            raise RuntimeError(
                "AsyncClient must be used within 'async with' statement. "
                "Use 'async with AsyncClient(...) as client:' to properly initialize."
            )
        if (
            self._discovered_metadata is not None
            and not self._discovered_metadata.token_endpoint
        ):
            return await self._get_current_endpoints()
        return self._discovered_endpoints or self._endpoints

    async def _get_current_endpoints(self) -> ResolvedEndpoints:
        """Get current endpoints from cached discovery.

        Discovered metadata is reused until ``discovery_ttl`` elapses, then
        re-discovered on the next call.
        """
        await self._ensure_initialized()
        metadata = await self._resolve_metadata()
        return resolve_endpoints(self.issuer, self._endpoint_overrides, metadata)

    @overload
    async def register_client(
        self,
        request: ClientRegistrationRequest,
        /,
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
            ValidationError: If request model validation fails (e.g., empty required fields)
            OAuthHttpError: If registration endpoint returns HTTP error (4xx/5xx)
            OAuthProtocolError: If registration response is invalid or contains OAuth errors
        """
        if request is not None and client_registration_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = ClientRegistrationRequest(**client_registration_args)

        endpoints = await self._get_current_endpoints()

        ctx = build_http_context(
            endpoint=endpoints.register,
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=client_registration_args.get("timeout", self.config.timeout),
        )

        return await register_client_async(request, ctx)

    @overload
    async def discover_server_metadata(
        self,
        request: ServerMetadataRequest,
        /,
    ) -> AuthorizationServerMetadata: ...

    @overload
    async def discover_server_metadata(
        self,
        /,
        *,
        issuer: str | None = None,
    ) -> AuthorizationServerMetadata: ...

    async def discover_server_metadata(self, request: ServerMetadataRequest | None = None, /, **metadata_discovery_args) -> AuthorizationServerMetadata:
        """Discover OAuth 2.0 authorization server metadata.

        Either pass a fully-formed ServerMetadataRequest *or* call with keyword
        args for simple cases.

        Simple usage:
            metadata = await client.discover_server_metadata()
            print(f"Token endpoint: {metadata.token_endpoint}")

        With keyword arguments:
            metadata = await client.discover_server_metadata(
                issuer="https://custom.auth.example.com"
            )

        Explicit request:
            request = ServerMetadataRequest(issuer="https://auth.example.com")
            metadata = await client.discover_server_metadata(request)

        Args:
            request: Optional ServerMetadataRequest (defaults to the client's issuer if not provided)
            **metadata_discovery_args: Alternative to request - provide ``issuer``
                (``base_url`` is accepted as a deprecated alias)

        Returns:
            AuthorizationServerMetadata with discovered server capabilities

        Raises:
            TypeError: If both request and metadata_discovery_args are provided
            ConfigError: If issuer is empty
            OAuthHttpError: If discovery endpoint is unreachable
            OAuthProtocolError: If metadata format is invalid
            NetworkError: If network request fails
        """
        if request is not None and metadata_discovery_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            issuer = _resolve_discovery_issuer(metadata_discovery_args, self.issuer)
            request = ServerMetadataRequest(issuer=issuer)

        context = build_http_context(
            endpoint=self.issuer,
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=self.config.timeout,
        )

        return await discover_server_metadata_async(
            request=request,
            context=context,
        )

    async def userinfo(
        self,
        access_token: str,
        *,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> UserInfoResponse:
        """Fetch the signed-in user's identity claims from the UserInfo endpoint.

        Zone access tokens are authorization-only, so identity claims such as
        ``email`` live behind the issuer's ``userinfo_endpoint`` rather than in
        the token. The endpoint is resolved from server metadata: metadata the
        client already discovered is reused, otherwise discovery runs first --
        including when ``ClientConfig.enable_metadata_discovery`` is False. Pass
        ``metadata`` to avoid the discovery request entirely.

        Simple usage:
            async with AsyncClient("https://zone.keycard.cloud") as client:
                user = await client.userinfo(access_token)
                print(user.sub, user.claims.get("email"))

        With pre-discovered metadata:
            metadata = await client.discover_server_metadata()
            user = await client.userinfo(access_token, metadata=metadata)

        Args:
            access_token: Access token issued for the signed-in user.
            metadata: Optional pre-discovered server metadata. Discovery is
                skipped when provided.
            timeout: Optional request timeout override.

        Returns:
            UserInfoResponse with ``sub`` and all returned claims.

        Raises:
            ConfigError: If the metadata has no ``userinfo_endpoint``
            InvalidTokenError: If the access token is not accepted (HTTP 401)
            OAuthHttpError: If the UserInfo endpoint returns another non-2xx status
            OAuthProtocolError: If the response is not a JSON claims object with ``sub``
            NetworkError: If the network request fails
        """
        request = UserInfoRequest(access_token=access_token, timeout=timeout)

        if metadata is None:
            await self._ensure_initialized()
            metadata = await self._resolve_metadata() or await self.discover_server_metadata()

        ctx = build_http_context(
            endpoint=resolve_userinfo_endpoint(metadata),
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=timeout or self.config.timeout,
        )

        return await fetch_userinfo_async(request, ctx)

    @overload
    async def exchange_token(
        self,
        request: TokenExchangeRequest,
        /,
        *,
        issuer: str | None = None,
    ) -> TokenResponse: ...

    @overload
    async def exchange_token(
        self,
        /,
        *,
        issuer: str | None = None,
        subject_token: str,
        subject_token_type: str = ...,
        grant_type: str | None = None,
        resource: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
        requested_token_type: str | None = None,
        actor_token: str | None = None,
        actor_token_type: str | None = None,
        timeout: float | None = None,
        client_id: str | None = None,
        client_assertion_type: str | None = None,
        client_assertion: str | None = None,
    ) -> TokenResponse: ...

    async def exchange_token(self, request: TokenExchangeRequest | None = None, /, **token_exchange_args) -> TokenResponse:
        """Perform OAuth 2.0 Token Exchange.

        Either pass a fully-formed TokenExchangeRequest *or* call with keyword
        args for common cases.

        Simple usage (delegation):
            request = TokenExchangeRequest(
                subject_token="original_access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com"
            )
            response = await client.token_exchange(request)

        Or with keyword arguments:
            response = await client.token_exchange(
                subject_token="original_access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com"
            )

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
            response = await client.token_exchange(request)

        Args:
            request: TokenExchangeRequest with all exchange parameters
            issuer: Issuer URL selecting which credentials a zone-aware auth
                strategy (e.g. MultiZoneBasicAuth) applies for this call.
                Defaults to the client's issuer. Single-credential strategies
                ignore it. May be combined with either calling form.
            **token_exchange_args: Alternative to request - provide individual parameters

        Returns:
            TokenResponse with the exchanged token and metadata

        Raises:
            TypeError: If both request and token_exchange_args are provided
            ConfigError: If the request sets client_id with HTTP Basic
        """
        issuer = token_exchange_args.pop("issuer", None)
        if request is not None and token_exchange_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = TokenExchangeRequest(**token_exchange_args)

        target_issuer = self.issuer if issuer is None else issuer
        auth = self._exchange_auth
        auth_headers = resolve_token_exchange_auth_headers(request, auth, target_issuer)
        if self._exchange_actor is not None and request.actor_token is None:
            request = _add_exchange_actor(
                request, await self._exchange_actor.identity_token()
            )
        endpoints = await self._get_current_endpoints()

        if (
            self._credential is not None
            and not request.client_assertion
            and not auth_headers
        ):
            request = await _prepare_exchange_assertion(
                self._credential, request, self, target_issuer, endpoints
            )
            auth = NoneAuth()
            auth_headers = {}

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=auth,
            issuer=target_issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=token_exchange_args.get("timeout", self.config.timeout),
        )

        return await exchange_token_async(request, ctx, auth_headers=auth_headers)

    @overload
    async def client_credentials_grant(
        self,
        request: ClientCredentialsRequest,
        /,
        *,
        issuer: str | None = None,
    ) -> TokenResponse: ...

    @overload
    async def client_credentials_grant(
        self,
        /,
        *,
        issuer: str | None = None,
        resource: str | None = None,
        scope: str | None = None,
        client_assertion: str | None = None,
        client_assertion_type: str | None = None,
        client_id: str | None = None,
        timeout: float | None = None,
    ) -> TokenResponse: ...

    async def client_credentials_grant(self, request: ClientCredentialsRequest | None = None, /, **client_credentials_args) -> TokenResponse:
        """Perform OAuth 2.0 Client Credentials Grant.

        Either pass a fully-formed ClientCredentialsRequest *or* call with
        keyword args for common cases.

        Simple usage:
            response = await client.client_credentials_grant(
                resource="https://api.company.com/data",
                scope="read write"
            )

        Or with a request object:
            request = ClientCredentialsRequest(
                resource="https://api.company.com/data",
                scope="read write"
            )
            response = await client.client_credentials_grant(request)

        Args:
            request: ClientCredentialsRequest with all grant parameters
            issuer: Issuer URL selecting which credentials a zone-aware auth
                strategy (e.g. MultiZoneBasicAuth) applies for this call.
                Defaults to the client's issuer. Single-credential strategies
                ignore it. May be combined with either calling form.
            client_id: Client identifier sent in the request body. Required when a
                federation-rule workload identity credential presents a jwt-bearer
                client_assertion and the zone resolves the application credential by
                application ID.
            **client_credentials_args: Alternative to request - provide individual parameters

        Returns:
            TokenResponse with the issued token and metadata

        Raises:
            TypeError: If both request and client_credentials_args are provided
        """
        issuer = client_credentials_args.pop("issuer", None)
        if request is not None and client_credentials_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = ClientCredentialsRequest(**client_credentials_args)

        authenticator = (
            _grant_authenticator(self._credential, interactive=False)
            if request.client_assertion is None
            else None
        )
        endpoints = await self._get_current_endpoints()
        target_issuer = issuer if issuer is not None else self.issuer
        client_auth = (
            await authenticator.client_authentication(
                issuer=target_issuer, endpoint=endpoints.token
            )
            if authenticator is not None
            else None
        )

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=target_issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=client_credentials_args.get("timeout", self.config.timeout),
        )

        return await client_credentials_grant_async(request, ctx, client_auth)

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        client_id: str | None = None,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        resource: str | None = None,
        timeout: float | None = None,
    ) -> TokenResponse:
        """Exchange an authorization code for tokens.

        Supports both public clients (client_id in the body, no auth header)
        and confidential clients (auth via the client's auth strategy). With
        ``credential``, the request has the credential's client
        authentication and the client's auth strategy is not used.

        Args:
            code: The authorization code from the callback.
            redirect_uri: The redirect URI used in the authorize request.
            code_verifier: The PKCE code verifier.
            client_id: Client ID for the form body. Required for public
                clients. Optional for confidential clients where identity
                is provided via the auth strategy.
            credential: A credential that provides client authentication
                (ClientSecret, WebIdentity, WorkloadIdentity). Cannot be
                combined with ``client_id``. It must authenticate as the
                client the code was issued to; the zone rejects any other
                client with ``invalid_grant``.
            resource: Optional RFC 8707 resource indicator. Scopes the
                issued token to a specific resource.
            timeout: Optional request timeout override.

        Returns:
            TokenResponse with access token and metadata.

        Raises:
            ConfigError: ``credential`` is combined with ``client_id``, or
                does not provide client authentication. No request is sent.
            OAuthHttpError: If the token endpoint returns an HTTP error.
            OAuthProtocolError: If the response contains an OAuth error.
            Exception: The credential's client authentication failed. Its
                error propagates as is. A multi-zone ClientSecret raises
                ConfigError for an unknown zone.
        """
        _warn_per_call_authentication("AsyncClient.exchange_authorization_code", credential, None)
        if credential is None and client_id is None and self._credential is None:
            client_id = self._public_client_id
        authenticator = _grant_authenticator(self._credential, credential, client_id)

        endpoints = await self._get_current_endpoints()

        client_auth: ClientAuthentication | None = None
        auth = self.auth_strategy
        if authenticator is not None:
            client_auth = await authenticator.client_authentication(
                issuer=self.issuer, endpoint=endpoints.token
            )
            auth = NoneAuth()

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=auth,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=timeout or self.config.timeout,
        )

        return await _exchange_authorization_code_async(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            client_id=client_id,
            context=ctx,
            resource=resource,
            client_auth=client_auth,
        )

    async def refresh_token_grant(
        self,
        *,
        refresh_token: str,
        client_id: str | None = None,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        resources: list[str] | None = None,
        scopes: list[str] | None = None,
        timeout: float | None = None,
    ) -> TokenResponse:
        """Redeem a refresh token for a new access token (RFC 6749 Section 6).

        A public client sends ``client_id`` in the body with no auth header.
        A confidential client authenticates through the client's auth
        strategy and passes no ``client_id``. With ``credential``, the
        request has the credential's client authentication and the client's
        auth strategy is not used.

        Args:
            refresh_token: The refresh token from an earlier token response.
            client_id: Client ID for the form body. Required for public
                clients, omitted for confidential clients.
            credential: A credential that provides client authentication
                (ClientSecret, WebIdentity, WorkloadIdentity). Cannot be
                combined with ``client_id``.
            resources: RFC 8707 resource indicators, sent as one ``resource``
                parameter per entry.
            scopes: Scopes to request, space-joined into ``scope``.
            timeout: Optional request timeout override.

        Returns:
            TokenResponse. When it has a ``refresh_token`` the server
            rotated it, and the caller must store it in place of the old
            one; the SDK keeps no state.

        Raises:
            ConfigError: ``credential`` is combined with ``client_id``, or
                does not provide client authentication. No request is sent.
            RefreshGrantError: If the token endpoint answers with an OAuth
                error. A 429 or 5xx is retryable. Otherwise ``invalid_grant``
                is not, and the user must authorize again.
            OAuthHttpError: If the token endpoint returns an HTTP error.
            Exception: The credential's client authentication failed. Its
                error propagates as is. A multi-zone ClientSecret raises
                ConfigError for an unknown zone.
        """
        _warn_per_call_authentication("AsyncClient.refresh_token_grant", credential, None)
        if credential is None and client_id is None and self._credential is None:
            client_id = self._public_client_id
        authenticator = _grant_authenticator(self._credential, credential, client_id)

        endpoints = await self._get_current_endpoints()

        client_auth: ClientAuthentication | None = None
        auth = self.auth_strategy
        if authenticator is not None:
            client_auth = await authenticator.client_authentication(
                issuer=self.issuer, endpoint=endpoints.token
            )
            auth = NoneAuth()

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=auth,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=timeout or self.config.timeout,
        )

        return await _refresh_token_grant_async(
            refresh_token=refresh_token,
            client_id=client_id,
            context=ctx,
            resources=resources,
            scope=" ".join(scopes) if scopes else None,
            client_auth=client_auth,
        )

    def _advertised_endpoint(
        self, metadata: AuthorizationServerMetadata | None, name: str
    ) -> str | None:
        """Return ``device_authorization_endpoint``, ``token_endpoint``, or
        ``revocation_endpoint``.

        The token and revocation endpoints come from the client's endpoint
        overrides first. None of them comes from a conventional path.
        """
        overrides = self._endpoint_overrides
        if name == "token_endpoint":
            if overrides and overrides.token:
                return overrides.token
            return metadata.token_endpoint if metadata else None
        if name == "revocation_endpoint":
            if overrides and overrides.revoke:
                return overrides.revoke
            return metadata.revocation_endpoint if metadata else None
        return metadata.device_authorization_endpoint if metadata else None

    async def _resolve_advertised_endpoint(
        self, metadata: AuthorizationServerMetadata | None, name: str
    ) -> tuple[str, str]:
        """Return the issuer and the URL of the endpoint ``name``.

        Pre-discovered ``metadata`` is used as is, with no discovery request.
        Its issuer must match the client's issuer, ignoring a trailing slash;
        the client's issuer is the one returned. A token or revocation
        endpoint override wins over ``metadata`` and discovery alike.
        Otherwise the endpoint comes from discovered metadata. When that
        metadata fails the endpoint check, the client discovers again once
        the metadata is older than ``negative_ttl``, so a failed check is
        remembered for at most ``negative_ttl``. If that discovery fails
        with a transient error, the cached metadata stays for the client's
        other operations.

        Raises:
            ConfigError: ``metadata`` names a different issuer, or the
                endpoint is missing or is not an http or https URL.
            AuthorizationServerDiscoveryError: Discovery failed.
        """
        if metadata is not None:
            if metadata.issuer.rstrip("/") != self.issuer.rstrip("/"):
                raise ConfigError(
                    f"issuer {self.issuer} does not match the metadata issuer "
                    f"{metadata.issuer}"
                )
            url = self._advertised_endpoint(metadata, name)
            return self.issuer, check_endpoint(self.issuer, name, url)

        discovered = await self._resolve_metadata()
        try:
            url = self._advertised_endpoint(discovered, name)
            return self.issuer, check_endpoint(self.issuer, name, url)
        except ConfigError:
            age = self._metadata_cache.success_age()
            if age is None or age < self.config.negative_ttl:
                raise
        discovered = await self._resolve_metadata(refresh=True)
        url = self._advertised_endpoint(discovered, name)
        return self.issuer, check_endpoint(self.issuer, name, url)

    async def _send_device_request(
        self, request: HttpRequest, timeout: float | None, operation: str
    ) -> HttpResponse:
        """Send one device grant request, bounded by the request timeout.

        The request gets ``config.user_agent`` and ``config.custom_headers``,
        as every other operation does. The request's own headers, including
        client authentication, win over both.

        Raises:
            DeviceAuthorizationError: A successful response whose body does
                not decode (``invalid_response``).
            OAuthHttpError: A 4xx or 5xx response whose body does not decode.
            NetworkError: The request failed or timed out.
        """
        headers = {
            "User-Agent": self.config.user_agent,
            **(self.config.custom_headers or {}),
            **request.headers,
        }
        try:
            return await self.transport.request_raw(
                replace(request, headers=headers),
                timeout=timeout or self.config.timeout,
            )
        except ResponseBodyTooLargeError as e:
            raise DeviceAuthorizationError(
                error="invalid_response",
                error_description=e.error_description,
                operation=operation,
                status_code=e.status_code,
            ) from e
        except ResponseBodyDecodeError as e:
            raise undecodable_body_error(e, operation) from e

    async def _authenticate_and_send(
        self,
        authenticator: ClientAuthenticator,
        issuer: str,
        endpoint: str,
        build: Callable[[ClientAuthentication], HttpRequest],
        timeout: float | None,
        operation: str,
    ) -> "HttpResponse | _ClientAuthenticationFailure":
        """Call the credential's client authentication, then send the request.

        The request timeout bounds the credential call and the request
        together. Running out of time raises NetworkError.

        Returns:
            The response, or a _ClientAuthenticationFailure when the
            credential raised. The caller raises its ``error`` unchanged.

        Raises:
            NetworkError: The request failed, or the timeout ran out.
            DeviceAuthorizationError, OAuthHttpError: As for
                _send_device_request.
        """
        limit = timeout or self.config.timeout

        async def run() -> "HttpResponse | _ClientAuthenticationFailure":
            try:
                client_auth = await authenticator.client_authentication(
                    issuer=issuer, endpoint=endpoint
                )
            except Exception as e:
                return _ClientAuthenticationFailure(e)
            return await self._send_device_request(build(client_auth), limit, operation)

        try:
            return await _run_with_timeout(run(), limit)
        except asyncio.TimeoutError as e:
            raise NetworkError(cause=e, operation=operation, retriable=False) from e

    async def request_device_authorization(
        self,
        *,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        client_id: str | None = None,
        scopes: list[str] | None = None,
        resources: list[str] | None = None,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> DeviceAuthorization:
        """Ask the zone for a device code and a user code (RFC 8628 Section 3.1).

        Use the client's configured credential, or pass ``credential`` or a
        public ``client_id`` explicitly. Use the same authentication for
        every poll of this flow. The client's auth strategy is not used.

        The SDK does not deliver the user code. Show the person
        ``verification_uri_complete`` when it is set, and
        ``verification_uri`` otherwise, together with ``user_code``.

        Args:
            credential: A credential that provides client authentication.
                Each request gets a new assertion whose ``aud`` is the
                endpoint it goes to.
            client_id: A public client's ID, sent in the body with no other
                authentication.
            scopes: Scopes to request, space-joined into ``scope``.
            resources: RFC 8707 resource indicators, one ``resource``
                parameter per entry.
            metadata: Pre-discovered authorization server metadata. No
                discovery request is made. Its issuer must match the
                client's issuer, ignoring a trailing slash, and the
                credential gets the client's issuer.
            timeout: Request timeout. It bounds the credential's client
                authentication and the request together. Defaults to the
                client's timeout.

        Returns:
            DeviceAuthorization, with ``expires_at`` read from the client's
            clock.

        Raises:
            ConfigError: The client has no authentication for this flow,
                both per-call inputs are set, the credential does not provide client
                authentication, ``metadata`` names a different issuer, or
                ``device_authorization_endpoint`` is missing or is not an
                http or https URL. No request is sent.
            AuthorizationServerDiscoveryError: Discovery failed.
            DeviceAuthorizationError: An OAuth error response, or a
                malformed response (``invalid_response``).
            OAuthHttpError: A 4xx or 5xx response without an OAuth error
                body.
            NetworkError: The request failed, or the credential's client
                authentication and the request ran out of time.
        """
        _warn_per_call_authentication(
            "AsyncClient.request_device_authorization", credential, client_id
        )
        authenticator = _device_client_authenticator(
            credential, client_id, self._credential, self._public_client_id
        )
        issuer, endpoint = await self._resolve_advertised_endpoint(
            metadata, "device_authorization_endpoint"
        )
        response = await self._authenticate_and_send(
            authenticator,
            issuer,
            endpoint,
            lambda client_auth: build_device_authorization_http_request(
                endpoint=endpoint,
                client_auth=client_auth,
                scopes=scopes,
                resources=resources,
            ),
            timeout,
            DEVICE_AUTHORIZATION_OPERATION,
        )
        if isinstance(response, _ClientAuthenticationFailure):
            raise response.error
        return parse_device_authorization_http_response(
            response, received_at=self.clock.now()
        )

    async def _poll_device_token_once(
        self,
        authenticator: ClientAuthenticator,
        issuer: str,
        token_endpoint: str,
        device_code: str,
        timeout: float | None,
    ) -> "TokenResponse | _ClientAuthenticationFailure":
        response = await self._authenticate_and_send(
            authenticator,
            issuer,
            token_endpoint,
            lambda client_auth: build_device_token_http_request(
                endpoint=token_endpoint, device_code=device_code, client_auth=client_auth
            ),
            timeout,
            DEVICE_TOKEN_OPERATION,
        )
        if isinstance(response, _ClientAuthenticationFailure):
            return response
        return parse_device_token_http_response(response)

    async def poll_device_token(
        self,
        authorization: DeviceAuthorization,
        *,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        client_id: str | None = None,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> TokenResponse:
        """Poll the token endpoint once with ``authorization.device_code``.

        Authenticate the same way as the device authorization request. A
        caller that repeats this in its own loop waits ``interval`` between
        polls, adds 5 seconds on ``slow_down``, and waits longer after a
        network error (RFC 8628 Section 3.5).

        Args:
            authorization: The device authorization response. Only
                ``device_code`` is read.
            credential: As for request_device_authorization.
            client_id: As for request_device_authorization.
            metadata: Pre-discovered authorization server metadata.
            timeout: Request timeout. It bounds the credential's client
                authentication and the request together. Defaults to the
                client's timeout.

        Returns:
            The token, with ``refresh_token`` and ``id_token`` as received.

        Raises:
            ConfigError: As for request_device_authorization, for
                ``token_endpoint``, or ``authorization.device_code`` is
                empty. No request is sent.
            AuthorizationServerDiscoveryError: Discovery failed.
            DeviceAuthorizationError: An OAuth error response, including
                ``authorization_pending`` and ``slow_down``, or a malformed
                response (``invalid_response``).
            OAuthHttpError: A 4xx or 5xx response without an OAuth error
                body.
            NetworkError: The request failed, or the credential's client
                authentication and the request ran out of time.
        """
        _warn_per_call_authentication(
            "AsyncClient.poll_device_token", credential, client_id
        )
        authenticator = _device_client_authenticator(
            credential, client_id, self._credential, self._public_client_id
        )
        if not authorization.device_code:
            raise ConfigError("authorization.device_code is empty")
        issuer, endpoint = await self._resolve_advertised_endpoint(
            metadata, "token_endpoint"
        )
        token = await self._poll_device_token_once(
            authenticator, issuer, endpoint, authorization.device_code, timeout
        )
        if isinstance(token, _ClientAuthenticationFailure):
            raise token.error
        return token

    async def wait_for_device_token(
        self,
        authorization: DeviceAuthorization,
        *,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        client_id: str | None = None,
        interval: int | None = None,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> TokenResponse:
        """Poll until the person approves or denies (RFC 8628 Section 3.5).

        Waits the interval before each poll, including the first. On
        ``authorization_pending`` it polls again. On ``slow_down`` it adds 5
        seconds to the interval for the next poll and every later one. Any other
        OAuth error stops the loop. A network error, a 429, or a 5xx stops
        the loop with DevicePollError. The loop never repeats a failed poll.

        Waits go through the client's clock. Cancelling the task ends the
        wait or the request in progress, and the loop does not call the
        credential or send another request after it. ``timeout`` bounds
        each poll: the credential's client authentication and the request
        together. It is also the httpx timeout of the request (connect,
        read, write, and pool). It holds when the caller sets a longer
        deadline on the whole loop, and it does not apply to the waits or
        the loop.

        When the device authorization request named more than one resource,
        the token is not for any one of them, even if ``raw["resource"]``
        names one. NEVER store it as the token for a single resource. To get
        a token for one resource, refresh with that resource.

        Args:
            authorization: The device authorization response, or one built
                from persisted fields.
            credential: As for request_device_authorization.
            client_id: As for request_device_authorization.
            interval: Seconds to wait before the first poll, and the
                starting interval. Defaults to ``authorization.interval``.
                To resume after DevicePollError, pass its ``interval``.
            metadata: Pre-discovered authorization server metadata.
            timeout: Timeout for each poll. It bounds the credential's
                client authentication and the request together. Defaults to
                the client's timeout.

        Returns:
            The token, with ``refresh_token`` and ``id_token`` as received.

        Raises:
            ConfigError: As for poll_device_token, or
                ``authorization.device_code`` is empty, or
                ``authorization.interval`` is not positive, or ``interval``
                is not an integer or is below it. No request is sent.
            AuthorizationServerDiscoveryError: Discovery failed.
            DeviceAuthorizationError: A terminal OAuth error such as
                ``access_denied``, ``invalid_grant``, or ``expired_token``,
                or a malformed response (``invalid_response``).
                ``expired_token`` is also raised, without sending a poll,
                when the next poll would start after ``expires_at``.
            DevicePollError: A poll failed with a network error, a 429, or a
                5xx, including a 429 or 5xx with an OAuth error body. Its
                ``interval`` is the interval to resume with. A poll that runs
                out of time is a NetworkError, also when the credential's
                client authentication does not return in time, so its
                ``interval`` is double the current interval.
            OAuthHttpError: A 4xx other than 429 without an OAuth error body.
            Exception: The credential's client authentication raised. Its
                error propagates unchanged, without ``interval``, even when
                it is a NetworkError or a TimeoutError.
            asyncio.CancelledError: The task was cancelled.
        """
        _warn_per_call_authentication(
            "AsyncClient.wait_for_device_token", credential, client_id
        )
        authenticator = _device_client_authenticator(
            credential, client_id, self._credential, self._public_client_id
        )
        if not authorization.device_code:
            raise ConfigError("authorization.device_code is empty")
        if authorization.interval < 1:
            raise ConfigError(
                f"authorization.interval {authorization.interval} is not positive"
            )
        if interval is None:
            interval = authorization.interval
        elif not isinstance(interval, int) or isinstance(interval, bool):
            raise ConfigError(f"interval {interval!r} is not an integer")
        elif interval < authorization.interval:
            raise ConfigError(
                f"interval {interval} is below the device authorization "
                f"interval {authorization.interval}"
            )
        issuer, endpoint = await self._resolve_advertised_endpoint(
            metadata, "token_endpoint"
        )

        def expired(description: str) -> DeviceAuthorizationError:
            return DeviceAuthorizationError(
                error="expired_token",
                error_description=description,
                operation=DEVICE_TOKEN_OPERATION,
            )

        while True:
            remaining = (authorization.expires_at - self.clock.now()).total_seconds()
            if interval > remaining:
                raise expired("The device code expires before the next poll")
            await self.clock.sleep(interval)
            # asyncio delivers a cancellation only where the task suspends.
            # This yield stops the loop here when the task was cancelled as
            # the wait ended, before the credential or the transport runs.
            await asyncio.sleep(0)
            if self.clock.now() > authorization.expires_at:
                raise expired("The device code expired during the wait before the next poll")

            try:
                token = await self._poll_device_token_once(
                    authenticator, issuer, endpoint, authorization.device_code, timeout
                )
                if not isinstance(token, _ClientAuthenticationFailure):
                    return token
            except NetworkError as e:
                raise DevicePollError(interval * 2, e) from e
            except (DeviceAuthorizationError, OAuthHttpError) as e:
                if _is_transient_status(e.status_code):
                    raise DevicePollError(interval, e) from e
                if isinstance(e, DeviceAuthorizationError):
                    if e.error == "authorization_pending":
                        continue
                    if e.error == "slow_down":
                        interval += SLOW_DOWN_INCREMENT
                        continue
                raise
            raise token.error

    async def introspect_token(
        self,
        token: str | None = None,
        *,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        token_type_hint: str | TokenTypeHint | None = None,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> TokenIntrospection:
        """Check a token's current status with the issuer (RFC 7662).

        Uses the supplied or discovered metadata's ``introspection_endpoint``.
        Pass ``credential`` or configure a confidential credential on the
        client. The SDK neither caches results nor retries the request.
        """
        authenticator = _introspection_authenticator(
            token, self._credential if credential is None else credential
        )
        assert token is not None
        endpoint = (
            _introspection_endpoint(self.issuer, metadata)
            if metadata is not None
            else None
        )
        issuer = self._introspection_issuer
        limit = timeout or self.config.timeout
        try:
            client_auth = await _run_with_timeout(
                authenticator.client_authentication(issuer=issuer, endpoint=issuer), limit
            )
        except asyncio.TimeoutError as e:
            raise NetworkError(e, operation=INTROSPECTION_OPERATION) from e
        check_introspection_auth(client_auth)
        if endpoint is None:
            endpoint = _introspection_endpoint(
                self.issuer,
                await self._resolve_metadata(endpoint_name="introspection_endpoint"),
            )
        request = _introspection_request(
            self.config, endpoint, token, token_type_hint, client_auth
        )
        try:
            response = await self.transport.request_raw(request, timeout=limit)
        except ResponseBodyDecodeError as e:
            raise invalid_introspection_response(
                e.status, "Could not decode the introspection response."
            ) from e
        return parse_introspection_http_response(response)

    def _revocation_client_authenticator(
        self,
        client_id: str | None,
        client_secret: str | None,
        credential: object | None,
    ) -> ClientAuthenticator:
        """Return the client authentication for one revocation request.

        Raises:
            ConfigError: ``credential`` is combined with ``client_id`` or
                ``client_secret``, ``client_secret`` is set without
                ``client_id``, the credential does not provide client
                authentication, or nothing authenticates the client.
        """
        if credential is not None:
            if client_id is not None or client_secret is not None:
                raise ConfigError(
                    "Pass either `credential` or `client_id` and `client_secret`, "
                    "not both."
                )
            return require_client_authenticator(credential)
        if client_secret is not None and client_id is None:
            raise ConfigError("`client_secret` requires `client_id`.")
        if client_id is not None:
            if not client_id:
                raise ConfigError("`client_id` is empty.")
            if client_secret is not None:
                if not client_secret:
                    raise ConfigError("`client_secret` is empty.")
                return _StaticClientAuthentication(
                    BasicAuth(client_id, client_secret).apply_headers()
                )
            return _PublicClient(client_id)
        try:
            headers = dict(self.auth_strategy.apply_headers(self.issuer))
        except KeyError as e:
            raise ConfigError(
                f"The client's auth strategy has no credentials for {self.issuer}."
            ) from e
        if not headers:
            raise ConfigError(
                "Pass `client_id` or `credential`, or create the client with an "
                "auth strategy, to authenticate the revocation request."
            )
        return _StaticClientAuthentication(headers)

    async def revoke_token(
        self,
        token: str,
        *,
        token_type_hint: str | TokenTypeHint = "refresh_token",
        client_id: str | None = None,
        client_secret: str | None = None,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> None:
        """Revoke a token (RFC 7009).

        Revoking a Keycard refresh token, or an access token with a ``sid``
        and no ``act``, revokes the token's session. The zone answers 200
        for an unknown, expired, already revoked, or unparseable token, so
        success does not mean a session was revoked.

        The endpoint is an ``Endpoints.revoke`` override, or else
        ``revocation_endpoint`` from ``metadata`` or discovery. With an
        override and no ``metadata``, no discovery request is made. There is
        no conventional path.

        Client authentication, in order of precedence:

        - ``credential``: its client authentication, with the zone issuer as
          both the issuer and the endpoint, so a private key JWT assertion
          has the issuer as its sole ``aud``.
        - ``client_id`` and ``client_secret``: HTTP Basic, with no
          ``client_id`` in the body.
        - ``client_id`` alone: a public client, ``client_id`` in the body.
        - Otherwise the client's auth strategy.

        The SDK does not retry. Async only.

        Args:
            token: The token to revoke.
            token_type_hint: Sent unchanged. Defaults to ``refresh_token``.
            client_id: A public or client secret client's ID.
            client_secret: With ``client_id``, authenticates with HTTP Basic.
            credential: A credential that provides client authentication
                (ClientSecret, WebIdentity, WorkloadIdentity). Cannot be
                combined with ``client_id`` or ``client_secret``.
            metadata: Pre-discovered authorization server metadata. No
                discovery request is made. Its issuer must match the
                client's issuer, ignoring a trailing slash.
            timeout: Request timeout. Defaults to the client's timeout.

        Raises:
            ConfigError: ``token`` is empty, the client authentication inputs
                are invalid, ``metadata`` names a different issuer, or
                ``revocation_endpoint`` is missing or is not an http or
                https URL. No revocation request is sent.
            AuthorizationServerDiscoveryError: Discovery failed.
            TokenRevocationError: An OAuth error response, such as
                ``invalid_client``, ``invalid_grant``, or
                ``unsupported_token_type``. A 429 or 5xx is retryable.
            OAuthHttpError: A response other than 2xx without an OAuth error
                body.
            NetworkError: The request failed or timed out.
        """
        if not token:
            raise ConfigError("`token` is empty.")
        authenticator = self._revocation_client_authenticator(
            client_id, client_secret, credential
        )
        override = self._endpoint_overrides.revoke if self._endpoint_overrides else None
        if metadata is None and override:
            issuer = self.issuer
            endpoint = check_endpoint(issuer, "revocation_endpoint", override)
        else:
            issuer, endpoint = await self._resolve_advertised_endpoint(
                metadata, "revocation_endpoint"
            )
        client_auth = await authenticator.client_authentication(
            issuer=issuer, endpoint=issuer
        )
        request = build_revocation_http_request(
            endpoint=endpoint,
            token=token,
            token_type_hint=token_type_hint,
            client_auth=client_auth,
        )
        headers = {
            "User-Agent": self.config.user_agent,
            **(self.config.custom_headers or {}),
            **request.headers,
        }
        try:
            response = await self.transport.request_raw(
                replace(request, headers=headers),
                timeout=timeout or self.config.timeout,
            )
        except ResponseBodyTooLargeError as e:
            if e.status_code is not None and 200 <= e.status_code < 300:
                return
            raise TokenRevocationError(
                error="invalid_response",
                error_description=e.error_description,
                operation=REVOCATION_OPERATION,
                status_code=e.status_code,
            ) from e
        except ResponseBodyDecodeError as e:
            if 200 <= e.status < 300:
                return
            raise OAuthHttpError(
                status_code=e.status,
                headers=e.headers,
                operation=REVOCATION_OPERATION,
            ) from e
        parse_revocation_http_response(response)

    async def impersonate(
        self,
        *,
        user_identifier: str,
        resource: str,
        scope: str | None = None,
        timeout: float | None = None,
        issuer: str | None = None,
        client_assertion: str | None = None,
        client_assertion_type: str | None = None,
    ) -> TokenResponse:
        """Impersonate a user to obtain a resource token.

        Performs an RFC 8693 token exchange using a substitute-user subject token.
        The calling application must authenticate via client credentials and the
        user must have previously established a delegated grant for the resource.

        This is a privileged operation controlled by policy. Impersonation is
        forbidden by default; an administrator must explicitly allow it.

        Args:
            user_identifier: Stable user identifier (e.g. email, oid).
            resource: Target resource URI (e.g. "https://graph.microsoft.com").
            scope: Optional scope string for the requested token.
            timeout: Optional request timeout override.
            issuer: Issuer URL selecting which credentials a zone-aware auth
                strategy (e.g. MultiZoneBasicAuth) applies for this call.
                Defaults to the client's issuer. Single-credential strategies
                ignore it.
            client_assertion: JWT client assertion authenticating the calling
                application in the request body, for credentials that carry no
                client secret (for example a workload identity token). Sent as
                the ``client_assertion`` form parameter, exactly as
                ``exchange_token()`` sends it.
            client_assertion_type: The assertion type URN, normally
                ``urn:ietf:params:oauth:client-assertion-type:jwt-bearer``.

        Returns:
            TokenResponse with the resource access token.

        Raises:
            OAuthHttpError: If the token endpoint returns an HTTP error.
            OAuthProtocolError: If the response contains an OAuth error.

        Example:
            async with AsyncClient(
                "https://zone.keycard.cloud",
                auth=BasicAuth("client_id", "client_secret"),
            ) as client:
                response = await client.impersonate(
                    user_identifier="user@example.com",
                    resource="https://graph.microsoft.com",
                )
                print(response.access_token)

            With a workload identity instead of a client secret, authenticate
            in the request body:
                response = await client.impersonate(
                    user_identifier="user@example.com",
                    resource="https://graph.microsoft.com",
                    client_assertion=workload_identity_token,
                    client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                )
        """
        request = TokenExchangeRequest(
            subject_token=build_substitute_user_token(user_identifier),
            subject_token_type=TokenType.SUBSTITUTE_USER,
            resource=resource,
            scope=scope,
            client_assertion=client_assertion,
            client_assertion_type=client_assertion_type,
        )

        target_issuer = issuer if issuer is not None else self.issuer
        auth = self._exchange_auth
        auth_headers = resolve_token_exchange_auth_headers(request, auth, target_issuer)
        endpoints = await self._get_current_endpoints()

        if (
            self._credential is not None
            and not request.client_assertion
            and not auth_headers
        ):
            request = await _prepare_exchange_assertion(
                self._credential, request, self, target_issuer, endpoints
            )
            auth = NoneAuth()
            auth_headers = {}

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=auth,
            issuer=target_issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=timeout or self.config.timeout,
        )

        return await exchange_token_async(request, ctx, auth_headers=auth_headers)

    def endpoints_summary(self) -> dict[str, dict[str, str]]:
        """Get diagnostic summary of resolved endpoints.

        Returns:
            Dictionary showing resolved URLs and their sources
        """
        return create_endpoints_summary(
            self._endpoints,
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
            client_id="my_client_id",
            client_secret="my_client_secret",
        ) as client:
            response = client.introspect_token("token_to_validate")

        # Enterprise usage with custom configuration
        client = Client(
            "https://api.keycard.ai",
            auth=BasicAuth("enterprise_client", "enterprise_secret"),
            endpoints=Endpoints(
                token="https://validator.internal.com/oauth2/token"
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
        issuer: str | None = None,
        *,
        base_url: str | None = None,
        auth: AuthStrategy | None = None,
        credential: "ApplicationCredential | None" = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        actor: "IdentityTokenSource | Callable[[], Awaitable[str] | str] | None" = None,
        endpoints: Endpoints | None = None,
        transport: HTTPTransport | None = None,
        config: ClientConfig | None = None,
    ):
        """Initialize synchronous OAuth 2.0 client.

        Args:
            issuer: Issuer URL of the OAuth 2.0 authorization server (RFC 8414).
            base_url: Deprecated alias for ``issuer``.
            auth: Authentication strategy (BasicAuth, BearerAuth, NoneAuth)
            credential: Application credential for grant operations. Cannot
                be combined with ``auth``.
            client_id: Client ID. With ``client_secret``, shorthand for a
                ClientSecret credential. Alone, the public client ID for
                device authorization, code exchange and refresh.
            client_secret: Client secret for shared-secret credential shorthand.
            actor: Identity token source or callable for delegation. It supplies
                a fresh actor token per exchange unless the request sets one.
                Impersonation does not use it. A sync callable runs on the
                calling thread and must enforce its own timeout if it blocks.
            endpoints: Endpoint overrides for multi-server deployments
            transport: Custom synchronous HTTP transport
            config: Client configuration with timeouts, retries, etc.
        """
        self.issuer = _resolve_issuer_arg(issuer, base_url)
        self._introspection_issuer = issuer or base_url or self.issuer
        self.config = config or ClientConfig()

        self.auth_strategy = auth or NoneAuth()
        credential, self._public_client_id = _resolve_grant_credential(
            credential, client_id, client_secret, auth
        )
        self._credential = credential
        self._exchange_actor = _actor_fetcher(actor)
        self._exchange_auth = (
            credential.get_http_client_auth()
            if auth is None and credential is not None
            else self.auth_strategy
        )

        if transport is not None:
            self.transport = transport
            self._owns_transport = False
        else:
            self.transport = HttpxTransport(
                config=self.config,
            )
            self._owns_transport = True

        self._endpoint_overrides = endpoints

        self._endpoints = resolve_endpoints(self.issuer, endpoints)

        # Lazy initialization state (thread-safe)
        self._initialized = False
        self._init_lock = threading.Lock()

        # Will be set during lazy initialization
        self._client_id = None
        self._client_secret = None
        self._discovered_endpoints: ResolvedEndpoints | None = None
        self._discovered_metadata: AuthorizationServerMetadata | None = None
        self._metadata_cache: MetadataCache[
            AuthorizationServerMetadata, AuthorizationServerDiscoveryError
        ] = MetadataCache(self.config.discovery_ttl, self.config.negative_ttl)
        self._discovery_lock = threading.Lock()

    def __repr__(self) -> str:
        return _client_repr(self)

    @property
    def base_url(self) -> str:
        """Deprecated alias for :attr:`issuer`. Use ``issuer`` instead."""
        return self.issuer

    def _resolve_metadata(
        self, *, endpoint_name: str | None = "token_endpoint"
    ) -> AuthorizationServerMetadata | None:
        """Return discovered metadata, or None when discovery is disabled.

        Success is cached for ``discovery_ttl``; a deterministic failure is
        remembered for at most ``negative_ttl``; transient failures can pace
        new fetches. Concurrent cold-cache threads serialize on one lock so a
        single discovery request serves all of them.
        Pass ``endpoint_name=None`` when no specific endpoint is needed.

        Raises:
            AuthorizationServerDiscoveryError: discovery failed. Never falls
                back to a guessed endpoint.
            MissingTokenEndpointError: a required token endpoint is missing.
        """
        if not self.config.enable_metadata_discovery:
            return None

        overridden = endpoint_name == "token_endpoint" and bool(
            self._endpoint_overrides and self._endpoint_overrides.token
        )
        cached = _cached_metadata_for_endpoint(
            self._metadata_cache, endpoint_name, overridden=overridden, refresh=False
        )
        if cached is not None:
            return _check_required_token_endpoint(
                self.issuer, cached, endpoint_name, overridden
            )

        with self._discovery_lock:
            metadata = _cached_metadata_for_endpoint(
                self._metadata_cache, endpoint_name, overridden=overridden, refresh=False
            )
            if metadata is None:
                try:
                    metadata = self.discover_server_metadata()
                except Exception as e:
                    raise self._store_discovery_failure(
                        AuthorizationServerDiscoveryError(self.issuer, cause=e)
                    ) from e
                self._store_discovered_metadata(metadata)
        return _check_required_token_endpoint(
            self.issuer, metadata, endpoint_name, overridden
        )

    def _store_discovery_failure(
        self, error: AuthorizationServerDiscoveryError
    ) -> AuthorizationServerDiscoveryError:
        return self._metadata_cache.store_failure(
            error, retryable=error.retryable, cause=error.cause
        )

    def _store_discovered_metadata(
        self, metadata: AuthorizationServerMetadata
    ) -> AuthorizationServerMetadata:
        overrides_token = self._endpoint_overrides and self._endpoint_overrides.token
        self._metadata_cache.store_success(metadata)
        self._discovered_metadata = metadata
        if metadata.token_endpoint or overrides_token:
            self._discovered_endpoints = resolve_endpoints(
                self.issuer, self._endpoint_overrides, metadata
            )
        return metadata

    def _ensure_initialized(self) -> None:
        """Ensure client is fully initialized with discovery and registration.

        This method performs lazy initialization in a thread-safe manner:
        1. Endpoint discovery (if enabled)
        2. Client registration (if auto_register_client is True)

        Uses double-check locking pattern for performance.

        Raises:
            AuthorizationServerDiscoveryError: metadata discovery failed. The
                client is left uninitialized and the next call discovers again
                (a deterministic failure is remembered for ``negative_ttl``).
        """
        # Fast path: already initialized
        if self._initialized:
            return

        # Slow path: acquire lock for initialization
        with self._init_lock:
            # Double-check: another thread might have initialized while we waited
            if self._initialized:
                return

            endpoint_name = "token_endpoint" if self.config.auto_register_client else None
            metadata = self._resolve_metadata(endpoint_name=endpoint_name)
            endpoints = resolve_endpoints(self.issuer, self._endpoint_overrides, metadata)
            self._discovered_endpoints = endpoints

            # Perform client registration if configured
            if self.config.auto_register_client:
                ctx = build_http_context(
                    endpoint=endpoints.register,
                    transport=self.transport,
                    auth=self.auth_strategy,
                    issuer=self.issuer,
                    user_agent=self.config.user_agent,
                    custom_headers=self.config.custom_headers,
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

    def _get_current_endpoints(self) -> ResolvedEndpoints:
        """Get current endpoints from cached discovery.

        Discovered metadata is reused until ``discovery_ttl`` elapses, then
        re-discovered on the next call.
        """
        self._ensure_initialized()
        metadata = self._resolve_metadata()
        return resolve_endpoints(self.issuer, self._endpoint_overrides, metadata)

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

    @property
    def endpoints(self) -> ResolvedEndpoints:
        """Resolved endpoint URLs (lazily initialized).

        Returns endpoints resolved during initialization, incorporating
        any discovered metadata and explicit overrides.

        Accessing this property will trigger automatic initialization if needed.

        Returns:
            Resolved Endpoints configuration.

        Raises:
            MissingTokenEndpointError: If discovery supplies no token endpoint
                and none is configured.
        """
        self._ensure_initialized()
        if (
            self._discovered_metadata is not None
            and not self._discovered_metadata.token_endpoint
        ):
            return self._get_current_endpoints()
        return self._discovered_endpoints or self._endpoints

    @overload
    def register_client(
        self,
        request: ClientRegistrationRequest,
        /,
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
            ValidationError: If request model validation fails (e.g., empty required fields)
            OAuthHttpError: If registration endpoint returns HTTP error (4xx/5xx)
            OAuthProtocolError: If registration response is invalid or contains OAuth errors
        """
        if request is not None and client_registration_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = ClientRegistrationRequest(**client_registration_args)

        endpoints = self._get_current_endpoints()

        ctx = build_http_context(
            endpoint=endpoints.register,
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=client_registration_args.get("timeout", self.config.timeout),
        )

        return register_client(request, ctx)

    @overload
    def discover_server_metadata(
        self,
        request: ServerMetadataRequest,
        /,
    ) -> AuthorizationServerMetadata: ...

    @overload
    def discover_server_metadata(
        self,
        /,
        *,
        issuer: str | None = None,
    ) -> AuthorizationServerMetadata: ...

    def discover_server_metadata(self, request: ServerMetadataRequest | None = None, /, **metadata_discovery_args) -> AuthorizationServerMetadata:
        """Discover OAuth 2.0 authorization server metadata.

        Either pass a fully-formed ServerMetadataRequest *or* call with keyword
        args for simple cases.

        Simple usage:
            metadata = client.discover_server_metadata()
            print(f"Token endpoint: {metadata.token_endpoint}")

        With keyword arguments:
            metadata = client.discover_server_metadata(
                issuer="https://custom.auth.example.com"
            )

        Explicit request:
            request = ServerMetadataRequest(issuer="https://auth.example.com")
            metadata = client.discover_server_metadata(request)

        Args:
            request: Optional ServerMetadataRequest (defaults to the client's issuer if not provided)
            **metadata_discovery_args: Alternative to request - provide ``issuer``
                (``base_url`` is accepted as a deprecated alias)

        Returns:
            AuthorizationServerMetadata with discovered server capabilities

        Raises:
            TypeError: If both request and metadata_discovery_args are provided
            OAuthHttpError: If discovery endpoint is unreachable
            OAuthProtocolError: If metadata format is invalid
            NetworkError: If network request fails
        """
        if request is not None and metadata_discovery_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            issuer = _resolve_discovery_issuer(metadata_discovery_args, self.issuer)
            request = ServerMetadataRequest(issuer=issuer)

        context = build_http_context(
            endpoint=self.issuer,
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=self.config.timeout,
        )

        return discover_server_metadata(
            request=request,
            context=context,
        )

    def introspect_token(
        self,
        token: str | None = None,
        *,
        credential: "ClientAuthenticator | ApplicationCredential | None" = None,
        token_type_hint: str | TokenTypeHint | None = None,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> TokenIntrospection:
        """Check a token's current status with the issuer (RFC 7662).

        Uses the supplied or discovered metadata's ``introspection_endpoint``.
        Pass ``credential`` or configure a confidential credential on the
        client. The SDK neither caches results nor retries the request.
        """
        authenticator = _introspection_authenticator(
            token, self._credential if credential is None else credential
        )
        assert token is not None
        endpoint = (
            _introspection_endpoint(self.issuer, metadata)
            if metadata is not None
            else None
        )
        issuer = self._introspection_issuer
        limit = timeout or self.config.timeout
        client_auth = _run_credential_operation(
            authenticator.client_authentication(issuer=issuer, endpoint=issuer),
            limit,
            "introspection client authentication",
            error_operation=INTROSPECTION_OPERATION,
        )
        check_introspection_auth(client_auth)
        if endpoint is None:
            endpoint = _introspection_endpoint(
                self.issuer,
                self._resolve_metadata(endpoint_name="introspection_endpoint"),
            )
        request = _introspection_request(
            self.config, endpoint, token, token_type_hint, client_auth
        )
        try:
            response = self.transport.request_raw(request, timeout=limit)
        except ResponseBodyDecodeError as e:
            raise invalid_introspection_response(
                e.status, "Could not decode the introspection response."
            ) from e
        return parse_introspection_http_response(response)

    def userinfo(
        self,
        access_token: str,
        *,
        metadata: AuthorizationServerMetadata | None = None,
        timeout: float | None = None,
    ) -> UserInfoResponse:
        """Fetch the signed-in user's identity claims from the UserInfo endpoint.

        Zone access tokens are authorization-only, so identity claims such as
        ``email`` live behind the issuer's ``userinfo_endpoint`` rather than in
        the token. The endpoint is resolved from server metadata: metadata the
        client already discovered is reused, otherwise discovery runs first --
        including when ``ClientConfig.enable_metadata_discovery`` is False. Pass
        ``metadata`` to avoid the discovery request entirely.

        Simple usage:
            with Client("https://zone.keycard.cloud") as client:
                user = client.userinfo(access_token)
                print(user.sub, user.claims.get("email"))

        With pre-discovered metadata:
            metadata = client.discover_server_metadata()
            user = client.userinfo(access_token, metadata=metadata)

        Args:
            access_token: Access token issued for the signed-in user.
            metadata: Optional pre-discovered server metadata. Discovery is
                skipped when provided.
            timeout: Optional request timeout override.

        Returns:
            UserInfoResponse with ``sub`` and all returned claims.

        Raises:
            ConfigError: If the metadata has no ``userinfo_endpoint``
            InvalidTokenError: If the access token is not accepted (HTTP 401)
            OAuthHttpError: If the UserInfo endpoint returns another non-2xx status
            OAuthProtocolError: If the response is not a JSON claims object with ``sub``
            NetworkError: If the network request fails
        """
        request = UserInfoRequest(access_token=access_token, timeout=timeout)

        if metadata is None:
            self._ensure_initialized()
            metadata = self._resolve_metadata() or self.discover_server_metadata()

        ctx = build_http_context(
            endpoint=resolve_userinfo_endpoint(metadata),
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=timeout or self.config.timeout,
        )

        return fetch_userinfo(request, ctx)

    @overload
    def exchange_token(
        self,
        request: TokenExchangeRequest,
        /,
        *,
        issuer: str | None = None,
    ) -> TokenResponse: ...

    @overload
    def exchange_token(
        self,
        /,
        *,
        issuer: str | None = None,
        subject_token: str,
        subject_token_type: str = ...,
        grant_type: str | None = None,
        resource: str | None = None,
        audience: str | None = None,
        scope: str | None = None,
        requested_token_type: str | None = None,
        actor_token: str | None = None,
        actor_token_type: str | None = None,
        timeout: float | None = None,
        client_id: str | None = None,
        client_assertion_type: str | None = None,
        client_assertion: str | None = None,
    ) -> TokenResponse: ...

    def exchange_token(self, request: TokenExchangeRequest | None = None, /, **token_exchange_args) -> TokenResponse:
        """Perform OAuth 2.0 Token Exchange.

        Either pass a fully-formed TokenExchangeRequest *or* call with keyword
        args for common cases.

        Simple usage (delegation):
            request = TokenExchangeRequest(
                subject_token="original_access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com"
            )
            response = client.token_exchange(request)

        Or with keyword arguments:
            response = client.token_exchange(
                subject_token="original_access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:access_token",
                audience="target-service.company.com"
            )

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
            issuer: Issuer URL selecting which credentials a zone-aware auth
                strategy (e.g. MultiZoneBasicAuth) applies for this call.
                Defaults to the client's issuer. Single-credential strategies
                ignore it. May be combined with either calling form.
            **token_exchange_args: Alternative to request - provide individual parameters

        Returns:
            TokenResponse with the exchanged token and metadata

        Raises:
            TypeError: If both request and token_exchange_args are provided
            ConfigError: If the request sets client_id with HTTP Basic
        """
        issuer = token_exchange_args.pop("issuer", None)
        if request is not None and token_exchange_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = TokenExchangeRequest(**token_exchange_args)

        target_issuer = self.issuer if issuer is None else issuer
        timeout = self.config.timeout if request.timeout is None else request.timeout
        auth = self._exchange_auth
        auth_headers = resolve_token_exchange_auth_headers(request, auth, target_issuer)
        if self._exchange_actor is not None and request.actor_token is None:
            token = self._exchange_actor.fetch()
            if not isinstance(token, str):
                token = _run_exchange_coroutine(
                    token,
                    timeout,
                    "actor token lookup",
                    timeout_error=_actor_lookup_timeout,
                )
            request = _add_exchange_actor(request, token)
        endpoints = self._get_current_endpoints()

        if (
            self._credential is not None
            and not request.client_assertion
            and not auth_headers
        ):
            request = _run_exchange_coroutine(
                _prepare_exchange_assertion(
                    self._credential, request, self, target_issuer, endpoints
                ),
                timeout,
                "assertion preparation",
            )
            auth = NoneAuth()
            auth_headers = {}

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=auth,
            issuer=target_issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=timeout,
        )

        return exchange_token(request, ctx, auth_headers=auth_headers)

    @overload
    def client_credentials_grant(
        self,
        request: ClientCredentialsRequest,
        /,
        *,
        issuer: str | None = None,
    ) -> TokenResponse: ...

    @overload
    def client_credentials_grant(
        self,
        /,
        *,
        issuer: str | None = None,
        resource: str | None = None,
        scope: str | None = None,
        client_assertion: str | None = None,
        client_assertion_type: str | None = None,
        client_id: str | None = None,
        timeout: float | None = None,
    ) -> TokenResponse: ...

    def client_credentials_grant(self, request: ClientCredentialsRequest | None = None, /, **client_credentials_args) -> TokenResponse:
        """Perform OAuth 2.0 Client Credentials Grant.

        Either pass a fully-formed ClientCredentialsRequest *or* call with
        keyword args for common cases.

        Simple usage:
            response = client.client_credentials_grant(
                resource="https://api.company.com/data",
                scope="read write"
            )

        Or with a request object:
            request = ClientCredentialsRequest(
                resource="https://api.company.com/data",
                scope="read write"
            )
            response = client.client_credentials_grant(request)

        Args:
            request: ClientCredentialsRequest with all grant parameters
            issuer: Issuer URL selecting which credentials a zone-aware auth
                strategy (e.g. MultiZoneBasicAuth) applies for this call.
                Defaults to the client's issuer. Single-credential strategies
                ignore it. May be combined with either calling form.
            client_id: Client identifier sent in the request body. Required when a
                federation-rule workload identity credential presents a jwt-bearer
                client_assertion and the zone resolves the application credential by
                application ID.
            **client_credentials_args: Alternative to request - provide individual parameters

        Returns:
            TokenResponse with the issued token and metadata

        Raises:
            TypeError: If both request and client_credentials_args are provided
        """
        issuer = client_credentials_args.pop("issuer", None)
        if request is not None and client_credentials_args:
            raise TypeError("Pass either `request` or keyword arguments, not both.")

        if request is None:
            request = ClientCredentialsRequest(**client_credentials_args)

        authenticator = (
            _grant_authenticator(self._credential, interactive=False)
            if request.client_assertion is None
            else None
        )
        endpoints = self._get_current_endpoints()
        target_issuer = issuer if issuer is not None else self.issuer
        client_auth = self._authenticate_grant(
            authenticator, target_issuer, endpoints.token, self.config.timeout
        )

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=self.auth_strategy,
            issuer=target_issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=client_credentials_args.get("timeout", self.config.timeout),
        )

        return client_credentials_grant(request, ctx, client_auth)

    def _authenticate_grant(
        self,
        authenticator: ClientAuthenticator | None,
        issuer: str,
        endpoint: str,
        timeout: float,
    ) -> ClientAuthentication | None:
        if authenticator is None:
            return None
        return _run_credential_operation(
            authenticator.client_authentication(issuer=issuer, endpoint=endpoint),
            timeout,
            "grant client authentication",
        )

    def exchange_authorization_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        client_id: str | None = None,
        resource: str | None = None,
        timeout: float | None = None,
    ) -> TokenResponse:
        """Exchange an authorization code for tokens.

        Supports both public clients (client_id in the body, no auth header)
        and confidential clients (auth via the client's auth strategy).

        Args:
            code: The authorization code from the callback.
            redirect_uri: The redirect URI used in the authorize request.
            code_verifier: The PKCE code verifier.
            client_id: Client ID for the form body. Required for public
                clients. Optional for confidential clients where identity
                is provided via the auth strategy.
            resource: Optional RFC 8707 resource indicator. Scopes the
                issued token to a specific resource.
            timeout: Optional request timeout override.

        Returns:
            TokenResponse with access token and metadata.

        Raises:
            OAuthHttpError: If the token endpoint returns an HTTP error.
            OAuthProtocolError: If the response contains an OAuth error.
        """
        if client_id is None and self._credential is None:
            client_id = self._public_client_id
        authenticator = _grant_authenticator(self._credential, client_id=client_id)
        endpoints = self._get_current_endpoints()
        request_timeout = timeout or self.config.timeout
        client_auth = self._authenticate_grant(
            authenticator, self.issuer, endpoints.token, request_timeout
        )

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=NoneAuth() if client_auth is not None else self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=request_timeout,
        )

        return _exchange_authorization_code(
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            client_id=client_id,
            context=ctx,
            resource=resource,
            client_auth=client_auth,
        )

    def refresh_token_grant(
        self,
        *,
        refresh_token: str,
        client_id: str | None = None,
        resources: list[str] | None = None,
        scopes: list[str] | None = None,
        timeout: float | None = None,
    ) -> TokenResponse:
        """Redeem a refresh token for a new access token (RFC 6749 Section 6).

        A public client sends ``client_id`` in the body with no auth header;
        a confidential client authenticates through the client's auth
        strategy and passes no ``client_id``.

        Args:
            refresh_token: The refresh token from an earlier token response.
            client_id: Client ID for the form body. Required for public
                clients, omitted for confidential clients.
            resources: RFC 8707 resource indicators, sent as one ``resource``
                parameter per entry.
            scopes: Scopes to request, space-joined into ``scope``.
            timeout: Optional request timeout override.

        Returns:
            TokenResponse. When it carries a ``refresh_token`` the server
            rotated it, and the caller must store it in place of the old
            one; the SDK keeps no state.

        Raises:
            RefreshGrantError: If the token endpoint answers with an OAuth
                error. A 429 or 5xx is retryable. Otherwise ``invalid_grant``
                is not, and the user must authorize again.
            OAuthHttpError: If the token endpoint returns an HTTP error.
        """
        if client_id is None and self._credential is None:
            client_id = self._public_client_id
        authenticator = _grant_authenticator(self._credential, client_id=client_id)
        endpoints = self._get_current_endpoints()
        request_timeout = timeout or self.config.timeout
        client_auth = self._authenticate_grant(
            authenticator, self.issuer, endpoints.token, request_timeout
        )

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=NoneAuth() if client_auth is not None else self.auth_strategy,
            issuer=self.issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=request_timeout,
        )

        return _refresh_token_grant(
            refresh_token=refresh_token,
            client_id=client_id,
            context=ctx,
            resources=resources,
            scope=" ".join(scopes) if scopes else None,
            client_auth=client_auth,
        )

    def impersonate(
        self,
        *,
        user_identifier: str,
        resource: str,
        scope: str | None = None,
        timeout: float | None = None,
        issuer: str | None = None,
        client_assertion: str | None = None,
        client_assertion_type: str | None = None,
    ) -> TokenResponse:
        """Impersonate a user to obtain a resource token.

        Performs an RFC 8693 token exchange using a substitute-user subject token.
        The calling application must authenticate via client credentials and the
        user must have previously established a delegated grant for the resource.

        This is a privileged operation controlled by policy. Impersonation is
        forbidden by default; an administrator must explicitly allow it.

        Args:
            user_identifier: Stable user identifier (e.g. email, oid).
            resource: Target resource URI (e.g. "https://graph.microsoft.com").
            scope: Optional scope string for the requested token.
            timeout: Optional request timeout override.
            issuer: Issuer URL selecting which credentials a zone-aware auth
                strategy (e.g. MultiZoneBasicAuth) applies for this call.
                Defaults to the client's issuer. Single-credential strategies
                ignore it.
            client_assertion: JWT client assertion authenticating the calling
                application in the request body, for credentials that carry no
                client secret (for example a workload identity token). Sent as
                the ``client_assertion`` form parameter, exactly as
                ``exchange_token()`` sends it.
            client_assertion_type: The assertion type URN, normally
                ``urn:ietf:params:oauth:client-assertion-type:jwt-bearer``.

        Returns:
            TokenResponse with the resource access token.

        Raises:
            OAuthHttpError: If the token endpoint returns an HTTP error.
            OAuthProtocolError: If the response contains an OAuth error.

        Example:
            with Client(
                "https://zone.keycard.cloud",
                auth=BasicAuth("client_id", "client_secret"),
            ) as client:
                response = client.impersonate(
                    user_identifier="user@example.com",
                    resource="https://graph.microsoft.com",
                )
                print(response.access_token)

            With a workload identity instead of a client secret, authenticate
            in the request body:
                response = client.impersonate(
                    user_identifier="user@example.com",
                    resource="https://graph.microsoft.com",
                    client_assertion=workload_identity_token,
                    client_assertion_type="urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                )
        """
        request = TokenExchangeRequest(
            subject_token=build_substitute_user_token(user_identifier),
            subject_token_type=TokenType.SUBSTITUTE_USER,
            resource=resource,
            scope=scope,
            client_assertion=client_assertion,
            client_assertion_type=client_assertion_type,
        )

        target_issuer = issuer if issuer is not None else self.issuer
        auth = self._exchange_auth
        auth_headers = resolve_token_exchange_auth_headers(request, auth, target_issuer)
        endpoints = self._get_current_endpoints()
        request_timeout = timeout or self.config.timeout

        if (
            self._credential is not None
            and not request.client_assertion
            and not auth_headers
        ):
            request = _prepare_exchange_assertion_sync(
                self._credential, request, self, target_issuer, endpoints, request_timeout
            )
            auth = NoneAuth()
            auth_headers = {}

        ctx = build_http_context(
            endpoint=endpoints.token,
            transport=self.transport,
            auth=auth,
            issuer=target_issuer,
            user_agent=self.config.user_agent,
            custom_headers=self.config.custom_headers,
            timeout=request_timeout,
        )

        return exchange_token(request, ctx, auth_headers=auth_headers)

    def endpoints_summary(self) -> dict[str, dict[str, str]]:
        """Get diagnostic summary of resolved endpoints.

        Returns:
            Dictionary showing resolved URLs and their sources
        """
        return create_endpoints_summary(
            self._endpoints,
            self._endpoint_overrides,
            None  # No cached metadata in simplified version
        )

    def __enter__(self):
        self._ensure_initialized()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        """Close the pooled HTTP connections held by this client's transport.

        Only a transport the client created itself is closed; an injected
        transport belongs to its owner. Closing is optional: an unclosed
        client releases its connections when it is garbage collected. The
        client stays usable after ``close()``; the next request opens a
        fresh connection pool.
        """
        if self._owns_transport and isinstance(self.transport, HttpxTransport):
            self.transport.close()
