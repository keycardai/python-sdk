"""Starlette/FastAPI AuthProvider with @protect() decorator.

Provides a framework-aware authentication provider that integrates with
Starlette and FastAPI applications. This is the protocol-agnostic equivalent
of MCP's AuthProvider — it does not depend on MCP Context or any MCP types.

Example::

    from fastapi import FastAPI, Request
    from keycardai.starlette import AuthProvider
    from keycardai.oauth.server import AccessContext, ClientSecret

    auth = AuthProvider(
        zone_id="your-zone-id",
        application_credential=ClientSecret(("client_id", "client_secret")),
    )

    app = FastAPI()
    auth.install(app)  # Adds BearerAuthMiddleware + .well-known endpoints

    @app.get("/api/calendar")
    @auth.protect("https://graph.microsoft.com")
    async def get_calendar(request: Request, access: AccessContext):
        token = access.access("https://graph.microsoft.com").access_token
        # call Microsoft Graph with token
"""

import asyncio
import inspect
import os
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic import AnyHttpUrl

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.http.auth import MultiZoneBasicAuth, NoneAuth
from keycardai.oauth.server.access_context import AccessContext
from keycardai.oauth.server.client_factory import ClientFactory, DefaultClientFactory
from keycardai.oauth.server.credentials import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from keycardai.oauth.server.exceptions import (
    AuthProviderConfigurationError,
    MissingAccessContextError,
)
from keycardai.oauth.server.token_exchange import exchange_tokens_for_resources
from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types.models import JsonWebKeySet
from starlette.requests import Request
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from .middleware import BearerAuthMiddleware
from .routers.metadata import auth_metadata_mount


class AuthProvider:
    """Keycard authentication provider for Starlette/FastAPI applications.

    Handles token verification, metadata discovery, and delegated token exchange
    without any MCP dependency.
    """

    def __init__(
        self,
        zone_id: str | None = None,
        zone_url: str | None = None,
        server_name: str | None = None,
        required_scopes: list[str] | None = None,
        audience: str | dict[str, str] | None = None,
        server_url: str | None = None,
        enable_multi_zone: bool = False,
        base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        enable_dynamic_client_registration: bool | None = None,
        application_credential: ApplicationCredential | None = None,
    ):
        """Initialize the Keycard auth provider.

        Args:
            zone_id: Keycard zone ID for OAuth operations.
            zone_url: Keycard zone URL. When enable_multi_zone=True,
                     this should be the top-level domain.
            server_name: Human-readable name for the server.
            required_scopes: Required scopes for token validation.
            audience: Expected token audience for verification.
            server_url: Resource server URL.
            enable_multi_zone: Enable multi-zone support.
            base_url: Base URL for Keycard (default: https://keycard.cloud).
            client_factory: Client factory for creating OAuth clients.
            enable_dynamic_client_registration: Override automatic registration.
            application_credential: Credential provider for token exchange.
        """
        zone_id = zone_id or os.getenv("KEYCARD_ZONE_ID")
        zone_url = zone_url or os.getenv("KEYCARD_ZONE_URL")
        base_url = base_url or os.getenv("KEYCARD_BASE_URL")
        server_url = server_url or os.getenv("SERVER_URL") or os.getenv("MCP_SERVER_URL")

        self.base_url = base_url or "https://keycard.cloud"

        if zone_url is None and not enable_multi_zone:
            if zone_id is None:
                raise AuthProviderConfigurationError()
            zone_url = f"{AnyHttpUrl(self.base_url).scheme}://{zone_id}.{AnyHttpUrl(self.base_url).host}"
        self.zone_url = zone_url
        self.issuer = self.zone_url or self.base_url
        self.server_name = server_name
        self.required_scopes = required_scopes
        self.server_url = server_url
        self.client_name = server_name or "OAuth Server Client"
        self.enable_multi_zone = enable_multi_zone
        self.client_factory = client_factory or DefaultClientFactory()
        self.enable_dynamic_client_registration = enable_dynamic_client_registration

        self._clients: dict[str, AsyncClient | None] = {}
        self._init_lock: asyncio.Lock | None = None
        self.audience = audience

        self.application_credential = self._discover_application_credential(
            application_credential
        )

        if self.application_credential is not None:
            self.auth = self.application_credential.get_http_client_auth()
        else:
            self.auth = NoneAuth()

        self.jwks: JsonWebKeySet | None = None
        if self.application_credential and hasattr(
            self.application_credential, "get_jwks"
        ):
            self.jwks = self.application_credential.get_jwks()

        self.enable_private_key_identity = isinstance(
            self.application_credential, WebIdentity
        )

    def _discover_application_credential(
        self, application_credential: ApplicationCredential | None
    ) -> ApplicationCredential | None:
        if application_credential is not None:
            return application_credential

        client_id = os.getenv("KEYCARD_CLIENT_ID")
        client_secret = os.getenv("KEYCARD_CLIENT_SECRET")
        if client_id and client_secret:
            return ClientSecret((client_id, client_secret))

        application_credential_type = os.getenv(
            "KEYCARD_APPLICATION_CREDENTIAL_TYPE"
        )
        if application_credential_type == "eks_workload_identity":
            custom_token_file_path = os.getenv(
                "KEYCARD_EKS_WORKLOAD_IDENTITY_TOKEN_FILE"
            )
            return EKSWorkloadIdentity(token_file_path=custom_token_file_path)
        elif application_credential_type == "web_identity":
            key_storage_dir = os.getenv("KEYCARD_WEB_IDENTITY_KEY_STORAGE_DIR")
            return WebIdentity(
                server_name=self.server_name,
                storage_dir=key_storage_dir,
            )
        elif application_credential_type is not None:
            raise AuthProviderConfigurationError(
                message=f"Unknown application credential type: {application_credential_type}. Supported types: eks_workload_identity, web_identity"
            )

        if any(
            os.getenv(env_name)
            for env_name in EKSWorkloadIdentity.default_env_var_names
        ):
            return EKSWorkloadIdentity()

        return None

    def _create_zone_scoped_url(self, base_url: str, zone_id: str) -> str:
        base_url_obj = AnyHttpUrl(base_url)
        port_part = ""
        if base_url_obj.port and not (
            (base_url_obj.scheme == "https" and base_url_obj.port == 443)
            or (base_url_obj.scheme == "http" and base_url_obj.port == 80)
        ):
            port_part = f":{base_url_obj.port}"
        return f"{base_url_obj.scheme}://{zone_id}.{base_url_obj.host}{port_part}"

    def _get_client_key(self, zone_id: str | None = None) -> str:
        if self.enable_multi_zone and zone_id:
            return f"zone:{zone_id}"
        return "default"

    async def _get_or_create_client(
        self, auth_info: dict[str, str] | None = None
    ) -> AsyncClient | None:
        client = None
        client_key = self._get_client_key(auth_info["zone_id"])
        if client_key in self._clients and self._clients[client_key] is not None:
            return self._clients[client_key]

        if self._init_lock is None:
            self._init_lock = asyncio.Lock()

        async with self._init_lock:
            if (
                client_key in self._clients
                and self._clients[client_key] is not None
            ):
                return self._clients[client_key]

            try:
                client_config = ClientConfig(
                    client_name=self.client_name,
                    enable_metadata_discovery=True,
                )

                if self.application_credential:
                    client_config = (
                        self.application_credential.set_client_config(
                            client_config, auth_info
                        )
                    )

                if self.enable_multi_zone and auth_info["zone_id"]:
                    base_url = self._create_zone_scoped_url(
                        self.base_url, auth_info["zone_id"]
                    )
                else:
                    base_url = self.zone_url

                auth_strategy = self.auth
                if isinstance(self.auth, MultiZoneBasicAuth) and auth_info[
                    "zone_id"
                ]:
                    if not self.auth.has_zone(auth_info["zone_id"]):
                        raise AuthProviderConfigurationError()
                    auth_strategy = self.auth.get_auth_for_zone(
                        auth_info["zone_id"]
                    )

                if self.enable_dynamic_client_registration is not None:
                    client_config.auto_register_client = (
                        self.enable_dynamic_client_registration
                    )
                elif self.application_credential is None and isinstance(
                    auth_strategy, NoneAuth
                ):
                    client_config.auto_register_client = True

                client = self.client_factory.create_async_client(
                    base_url=base_url,
                    auth=auth_strategy,
                    config=client_config,
                )
            finally:
                self._clients[client_key] = client
            return client

    def get_token_verifier(
        self, enable_multi_zone: bool | None = None
    ) -> TokenVerifier:
        """Get a token verifier for this provider."""
        if enable_multi_zone is None:
            enable_multi_zone = self.enable_multi_zone
        return TokenVerifier(
            required_scopes=self.required_scopes,
            issuer=self.issuer,
            enable_multi_zone=enable_multi_zone,
            audience=self.audience,
            client_factory=self.client_factory,
        )

    def get_routes(self, app: ASGIApp) -> list[Mount | Route]:
        """Get OAuth metadata routes and protected app mount.

        Returns a list of routes suitable for ``Starlette(routes=...)``.
        """
        from .routers.metadata import protected_router

        return list(
            protected_router(
                issuer=self.issuer,
                app=app,
                verifier=self.get_token_verifier(),
                enable_multi_zone=self.enable_multi_zone,
                jwks=self.jwks,
            )
        )

    def install(self, app: ASGIApp) -> None:
        """Install bearer auth middleware and metadata routes on an ASGI app.

        For FastAPI/Starlette apps, adds:
        - BearerAuthMiddleware for token verification
        - ``/.well-known/oauth-protected-resource`` endpoint
        - ``/.well-known/oauth-authorization-server`` endpoint
        - ``/.well-known/jwks.json`` endpoint (if WebIdentity is used)
        """
        verifier = self.get_token_verifier()
        app.add_middleware(BearerAuthMiddleware, verifier=verifier)

        metadata_routes = auth_metadata_mount(
            self.issuer,
            enable_multi_zone=self.enable_multi_zone,
            jwks=self.jwks,
        )
        app.routes.insert(0, metadata_routes)

    def protect(
        self,
        resources: str | list[str],
        user_identifier: Callable[..., str] | None = None,
    ):
        """Decorator for automatic delegated token exchange.

        The decorated function receives an ``AccessContext`` parameter populated
        with exchanged tokens for the requested resources.  Errors are stored
        per-resource rather than raised.

        Args:
            resources: Target resource URL(s) for token exchange.
            user_identifier: Optional callable that extracts a user identifier
                from the request kwargs for impersonation exchange.

        Example::

            @app.get("/api/calendar")
            @auth.protect("https://graph.microsoft.com")
            async def get_calendar(request: Request, access: AccessContext):
                token = access.access("https://graph.microsoft.com").access_token
                # Use token to call Microsoft Graph
        """

        def _get_param_info_by_type(
            func: Callable, param_type: type
        ) -> tuple[str, int] | None:
            sig = inspect.signature(func)
            for index, value in enumerate(sig.parameters.values()):
                if value.annotation == param_type:
                    return value.name, index
            return None

        def _get_safe_func_signature(
            func: Callable,
        ) -> inspect.Signature:
            sig = inspect.signature(func)
            safe_params = []
            for param in sig.parameters.values():
                if param.annotation == AccessContext:
                    continue
                safe_params.append(param)
            return sig.replace(parameters=safe_params)

        def _get_request(*args, **kwargs) -> Request | None:
            for value in args:
                if isinstance(value, Request):
                    return value
            for value in kwargs.values():
                if isinstance(value, Request):
                    return value
            return None

        def _set_error(
            error: dict[str, str],
            resource: str | None,
            access_context: AccessContext,
        ):
            if resource:
                access_context.set_resource_error(resource, error)
            else:
                access_context.set_error(error)

        async def _call_func(
            _is_async_func: bool, func: Callable, *args, **kwargs
        ):
            if _is_async_func:
                return await func(*args, **kwargs)
            else:
                return func(*args, **kwargs)

        def decorator(func: Callable) -> Callable:
            _is_async_func = inspect.iscoroutinefunction(func)

            _access_ctx_param_info = _get_param_info_by_type(
                func, AccessContext
            )
            if _access_ctx_param_info is None:
                raise MissingAccessContextError()

            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                # Inject or find AccessContext
                if (
                    _access_ctx_param_info[0] not in kwargs
                    or kwargs[_access_ctx_param_info[0]] is None
                ):
                    kwargs[_access_ctx_param_info[0]] = AccessContext()
                _access_ctx = kwargs[_access_ctx_param_info[0]]

                # Extract auth info from request state
                _keycardai_auth_info: dict[str, str] | None = None
                try:
                    request = _get_request(*args, **kwargs)
                    if request is None:
                        _set_error(
                            {
                                "message": "No Request found in function arguments. Ensure the function has a Request parameter."
                            },
                            None,
                            _access_ctx,
                        )
                        return await _call_func(
                            _is_async_func, func, *args, **kwargs
                        )
                    _keycardai_auth_info = getattr(
                        request.state, "keycardai_auth_info", None
                    )
                    if not _keycardai_auth_info:
                        _set_error(
                            {
                                "message": "No authentication info on request. Ensure BearerAuthMiddleware is installed."
                            },
                            None,
                            _access_ctx,
                        )
                        return await _call_func(
                            _is_async_func, func, *args, **kwargs
                        )

                    if not _keycardai_auth_info.get("access_token"):
                        _set_error(
                            {
                                "message": "No authentication token available."
                            },
                            None,
                            _access_ctx,
                        )
                        return await _call_func(
                            _is_async_func, func, *args, **kwargs
                        )
                except Exception as e:
                    _set_error(
                        {
                            "message": "Failed to extract auth info from request.",
                            "raw_error": str(e),
                        },
                        None,
                        _access_ctx,
                    )
                    return await _call_func(
                        _is_async_func, func, *args, **kwargs
                    )

                # Get or create OAuth client
                if (
                    self.enable_multi_zone
                    and not _keycardai_auth_info.get("zone_id")
                ):
                    _set_error(
                        {
                            "message": "Zone ID required for multi-zone configuration but not found."
                        },
                        None,
                        _access_ctx,
                    )
                    return await _call_func(
                        _is_async_func, func, *args, **kwargs
                    )
                try:
                    _client = await self._get_or_create_client(
                        _keycardai_auth_info
                    )
                    if _client is None:
                        _set_error(
                            {
                                "message": "OAuth client not available. Server configuration issue."
                            },
                            None,
                            _access_ctx,
                        )
                        return await _call_func(
                            _is_async_func, func, *args, **kwargs
                        )
                except Exception as e:
                    _set_error(
                        {
                            "message": "Failed to initialize OAuth client.",
                            "raw_error": str(e),
                        },
                        None,
                        _access_ctx,
                    )
                    return await _call_func(
                        _is_async_func, func, *args, **kwargs
                    )

                # Resolve user identifier for impersonation
                _resolved_user_id: str | None = None
                if user_identifier is not None:
                    try:
                        _resolved_user_id = user_identifier(**kwargs)
                    except Exception as e:
                        _set_error(
                            {
                                "message": "Failed to resolve user_identifier.",
                                "raw_error": str(e),
                            },
                            None,
                            _access_ctx,
                        )
                        return await _call_func(
                            _is_async_func, func, *args, **kwargs
                        )

                # Delegate to framework-free token exchange orchestration
                _resource_list = (
                    [resources]
                    if isinstance(resources, str)
                    else resources
                )
                await exchange_tokens_for_resources(
                    client=_client,
                    resources=_resource_list,
                    subject_token=_keycardai_auth_info["access_token"],
                    access_context=_access_ctx,
                    application_credential=self.application_credential,
                    auth_info=_keycardai_auth_info,
                    user_identifier=_resolved_user_id,
                )

                return await _call_func(
                    _is_async_func, func, *args, **kwargs
                )

            wrapper.__signature__ = _get_safe_func_signature(func)
            return wrapper

        return decorator
