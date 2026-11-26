"""Starlette/FastAPI AuthProvider integrated with Starlette's standard auth framework.

``AuthProvider.install(app)`` wires up
``starlette.middleware.authentication.AuthenticationMiddleware`` with a
``KeycardAuthBackend`` so ``request.user`` / ``request.auth`` are populated for
every request, and adds the RFC 9728 / RFC 8414 ``/.well-known/*`` discovery
routes. Routes that need authentication use the standard
``@requires("authenticated")`` decorator (Keycard's drop-in for
``starlette.authentication.requires`` that emits an RFC 6750
``WWW-Authenticate`` challenge instead of stock ``HTTPException(403)``).
Delegated OAuth 2.0 token exchange is requested with
``@auth.grant(resource_url)`` (mirroring ``keycardai.mcp``'s ``@grant()``).

Example::

    from fastapi import FastAPI, Request
    from keycardai.starlette import AuthProvider, KeycardUser, requires
    from keycardai.oauth.server import AccessContext, ClientSecret

    auth = AuthProvider(
        zone_id="your-zone-id",
        application_credential=ClientSecret(("client_id", "client_secret")),
        # Resource indicator (RFC 8707) Keycard mints tokens for. The verifier
        # rejects tokens whose "aud" claim does not include this value.
        resource_identifier="https://your-api.example.com/api/me",
    )

    app = FastAPI()
    auth.install(app)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/api/me")
    @requires("authenticated")
    async def me(request: Request):
        user: KeycardUser = request.user
        return {"client_id": user.client_id, "scopes": list(request.auth.scopes)}

    @app.get("/api/calendar")
    @requires("authenticated")
    @auth.grant("https://graph.microsoft.com")
    async def calendar(request: Request, access: AccessContext):
        token = access.access("https://graph.microsoft.com").access_token
"""

import asyncio
import os
import warnings
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from pydantic import AnyHttpUrl

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.http.auth import MultiZoneBasicAuth, NoneAuth
from keycardai.oauth.server.client_factory import ClientFactory, DefaultClientFactory
from keycardai.oauth.server.credentials import (
    ApplicationCredential,
    ClientSecret,
    WebIdentity,
    discover_credential,
)
from keycardai.oauth.server.exceptions import (
    AuthProviderConfigurationError,
    CredentialDiscoveryError,
    MissingAudienceWarning,
)
from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types.models import JsonWebKeySet
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Scope

from .authorization import grant as _grant_factory, requires as _requires_module_func
from .binding import ResourceBinding, ResourceBindingConfigError, zone_issuer
from .middleware._introspection import validate_credential
from .middleware.bearer import KeycardAuthBackend, keycard_on_error
from .routers.metadata import auth_metadata_mount


def _deprecated_zone_env(name: str) -> str | None:
    """Read a legacy zone variable, warning that KEYCARD_ZONE_URL replaces it."""
    value = os.getenv(name)
    if value:
        warnings.warn(
            f"{name} is deprecated and will be removed in a future release; "
            "set KEYCARD_ZONE_URL to the full zone URL instead.",
            DeprecationWarning,
            stacklevel=3,
        )
    return value


def _route_zone(scope: Scope, routes: list[BaseRoute]) -> str | None:
    for route in routes:
        match, child_scope = route.matches(scope)
        if match == Match.FULL:
            zone = child_scope.get("path_params", {}).get("zone_id")
            if zone is not None:
                return zone
            return _route_zone({**scope, **child_scope}, getattr(route, "routes", []))
    return None



@runtime_checkable
class _JwksCredential(Protocol):
    """Credential that publishes a JWKS (WebIdentity and private-key credentials)."""

    def get_jwks(self) -> JsonWebKeySet: ...

class AuthProvider:
    """Keycard authentication provider for Starlette and FastAPI applications.

    Handles token verification, OAuth metadata discovery, and delegated token
    exchange.
    """

    # Module-level ``requires`` exposed as a method-style alias so users who
    # prefer accessing the API through their AuthProvider instance can write
    # ``@auth.requires("authenticated")``. The function is stateless aside
    # from the request scope, so the alias and the module-level export
    # share one implementation.
    requires = staticmethod(_requires_module_func)

    def __init__(
        self,
        zone_id: str | None = None,
        zone_url: str | None = None,
        server_name: str | None = None,
        required_scopes: list[str] | None = None,
        audience: str | list[str] | dict[str, str] | None = None,
        server_url: str | None = None,
        enable_multi_zone: bool | None = None,
        base_url: str | None = None,
        client_factory: ClientFactory | None = None,
        enable_dynamic_client_registration: bool | None = None,
        application_credential: ApplicationCredential | None = None,
        *,
        resource_identifier: str | None = None,
        resource_identifiers_by_zone: dict[str, str] | None = None,
        multi_resource: bool = False,
        allow_any_audience: bool = False,
        introspect: bool = False,
        introspection_timeout: float = 10.0,
    ):
        """Initialize the Keycard auth provider.

        Args:
            zone_id: Keycard zone ID for OAuth operations.
            zone_url: Keycard zone URL. When enable_multi_zone=True,
                     this should be the top-level domain.
            server_name: Human-readable name for the server.
            required_scopes: Required scopes for token validation.
            audience: Expected token audience for verification. A list
                accepts a token whose ``aud`` matches any of its entries, and
                a dict maps each zone ID to its audience in multi-zone mode.
                With an explicit mode, supplied only for ``multi_resource``.
            server_url: Resource server URL.
            enable_multi_zone: Enable multi-zone support where zone_url is the
                top-level domain and the zone is extracted from request
                context. When left unset (None), it is inferred from the
                credential: a multi-zone ClientSecret turns it on
                automatically. Pass True/False to override.
            base_url: Base URL for Keycard (default: https://keycard.cloud).
            client_factory: Client factory for creating OAuth clients.
            enable_dynamic_client_registration: Override automatic registration.
            application_credential: Credential provider for token exchange.
            resource_identifier: Exact audience as an HTTP(S) URL or URN.
                HTTP(S) identifiers also publish protected-resource metadata.
            resource_identifiers_by_zone: Exact identifiers indexed by zone ID.
            multi_resource: Use a nonempty audience list. With a zone map,
                publish shared discovery for its one common HTTP(S) identifier.
            allow_any_audience: Skip only the audience check, with no
                protected-resource discovery.
            introspect: Check each locally verified token using the application
                credential that owns the resource.
            introspection_timeout: Bound each status check, including discovery, in seconds.
        """
        zone_url = zone_url or os.getenv("KEYCARD_ZONE_URL")
        zone_id = zone_id or _deprecated_zone_env("KEYCARD_ZONE_ID")
        base_url = base_url or _deprecated_zone_env("KEYCARD_BASE_URL")
        server_url = server_url or os.getenv("SERVER_URL") or os.getenv("MCP_SERVER_URL")

        self.base_url = base_url or "https://keycard.cloud"

        # Infer multi-zone from a multi-zone ClientSecret unless explicitly set.
        # Env-discovered credentials are always single-zone, so the passed
        # argument is the only source that can imply multi-zone.
        # Shared discovery selects the zone from the token, so a multi-zone
        # credential does not turn on request zone selection, and the
        # provider needs no default zone.
        shared = multi_resource and resource_identifiers_by_zone is not None
        if enable_multi_zone is None:
            enable_multi_zone = (
                not shared
                and isinstance(application_credential, ClientSecret)
                and application_credential.is_multi_zone
            )

        if zone_url is None and not enable_multi_zone and not shared:
            if zone_id is None:
                raise AuthProviderConfigurationError()
            zone_url = f"{AnyHttpUrl(self.base_url).scheme}://{zone_id}.{AnyHttpUrl(self.base_url).host}"
        self.zone_url = zone_url
        self.server_name = server_name
        self.required_scopes = required_scopes
        self.server_url = server_url
        self.client_name = server_name or "OAuth Server Client"
        self.enable_multi_zone = enable_multi_zone
        self.client_factory = client_factory or DefaultClientFactory()
        self.enable_dynamic_client_registration = enable_dynamic_client_registration

        self._clients: dict[str, AsyncClient | None] = {}
        self._init_lock = asyncio.Lock()
        if (
            isinstance(audience, list) and not audience
            and not multi_resource and not allow_any_audience
            and resource_identifier is None and resource_identifiers_by_zone is None
        ):
            raise AuthProviderConfigurationError(message="audience must not be an empty list")
        self.resource_binding = ResourceBinding(
            resource_identifier=resource_identifier,
            resource_identifiers_by_zone=resource_identifiers_by_zone,
            multi_resource=multi_resource,
            audience=audience,
            allow_any_audience=allow_any_audience,
        )
        self.resource_binding._validate_multi_zone(enable_multi_zone)
        if self.resource_binding.mode == "per_zone" and not enable_multi_zone:
            raise ResourceBindingConfigError("Per-zone binding requires enable_multi_zone")
        if self.resource_binding.mode == "shared" and enable_multi_zone:
            raise ResourceBindingConfigError(
                "Shared discovery requires token-keyed verification; "
                "leave enable_multi_zone unset or pass enable_multi_zone=False"
            )
        self.issuer = (
            self.base_url if self.resource_binding.mode == "shared" else self.zone_url or self.base_url
        )
        self.audience = (
            audience if self.resource_binding.mode == "legacy" else self.resource_binding.audience
        )

        self.application_credential = self._discover_application_credential(
            application_credential
        )
        self.introspect = introspect
        self.introspection_timeout = introspection_timeout
        if introspect:
            validate_credential(
                self.application_credential,
                multi_zone=enable_multi_zone or (
                    self.resource_binding.mode == "shared"
                    and len(self.resource_binding.resource_identifiers_by_zone) > 1
                ),
                timeout=introspection_timeout,
            )

        if self.application_credential is not None:
            self.auth = self.application_credential.get_http_client_auth()
        else:
            self.auth = NoneAuth()

        self.jwks: JsonWebKeySet | None = None
        if isinstance(self.application_credential, _JwksCredential):
            self.jwks = self.application_credential.get_jwks()

        self.enable_private_key_identity = isinstance(
            self.application_credential, WebIdentity
        )

    def _discover_application_credential(
        self, application_credential: ApplicationCredential | None
    ) -> ApplicationCredential | None:
        if application_credential is not None:
            return application_credential

        try:
            return discover_credential(web_identity_server_name=self.server_name)
        except CredentialDiscoveryError as e:
            if e.reason == CredentialDiscoveryError.ABSENT:
                return None
            raise AuthProviderConfigurationError(message=str(e)) from e

    def _create_zone_scoped_url(self, base_url: str, zone_id: str) -> str:
        return zone_issuer(base_url, zone_id)

    def _get_client_key(self, zone_id: str | None = None) -> str:
        if zone_id and (self.enable_multi_zone or self.resource_binding.mode == "shared"):
            return f"zone:{zone_id}"
        return "default"

    async def _get_or_create_client(
        self, auth_info: dict[str, str | None]
    ) -> AsyncClient | None:
        client = None
        zone_id = auth_info["zone_id"]
        shared = self.resource_binding.mode == "shared"
        if shared and (
            zone_id is None or zone_id not in self.resource_binding.resource_identifiers_by_zone
        ):
            raise AuthProviderConfigurationError("A trusted zone is required for shared discovery")
        client_key = self._get_client_key(zone_id)
        if client_key in self._clients and self._clients[client_key] is not None:
            return self._clients[client_key]

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

                if shared and zone_id:
                    base_url = self._create_zone_scoped_url(self.issuer, zone_id)
                elif self.enable_multi_zone and zone_id:
                    base_url = self._create_zone_scoped_url(self.base_url, zone_id)
                elif self.zone_url is not None:
                    base_url = self.zone_url
                else:
                    raise AuthProviderConfigurationError()

                auth_strategy = self.auth
                if isinstance(self.auth, MultiZoneBasicAuth) and zone_id:
                    # Multi-zone credentials are keyed by the zone's issuer
                    # URL, which is the same zone-scoped URL the client is
                    # created against.
                    if not self.auth.has_issuer(base_url):
                        raise AuthProviderConfigurationError()
                    auth_strategy = self.auth.get_auth_for_issuer(base_url)

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
        binding = self.resource_binding
        binding._validate_multi_zone(enable_multi_zone)
        issuer: str | list[str] = self.issuer
        if binding.mode == "per_zone" and not enable_multi_zone:
            raise ResourceBindingConfigError("Per-zone binding requires request zone selection")
        if binding.mode == "shared":
            if enable_multi_zone:
                raise ResourceBindingConfigError("Shared discovery requires token-keyed verification")
            issuer = list(binding.zone_issuers(issuer).values())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MissingAudienceWarning)
            return TokenVerifier(
                required_scopes=self.required_scopes,
                issuer=issuer,
                enable_multi_zone=enable_multi_zone,
                audience=self.audience if binding.mode == "legacy" else binding.audience,
                client_factory=self.client_factory,
            )

    def get_routes(self, app: ASGIApp) -> list[BaseRoute]:
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
                resource_binding=self.resource_binding,
                introspect=self.introspect,
                credential=self.application_credential,
                introspection_timeout=self.introspection_timeout,
            )
        )

    def install(self, app: Any) -> None:
        """Install OAuth metadata discovery routes and AuthenticationMiddleware.

        Adds the ``/.well-known/*`` metadata routes (RFC 9728, RFC 8414, and
        JWKS when configured) and registers
        ``starlette.middleware.authentication.AuthenticationMiddleware`` wired
        to ``KeycardAuthBackend``. After installation, every request passes
        through the backend; routes without an authentication requirement
        remain public to requests without an Authorization header.
        ``request.user`` and ``request.auth`` are populated for every route.

        Anonymous requests to protected routes receive an RFC 6750 401
        response with a Bearer challenge. Explicit publishing modes include
        the metadata URL only for the matching request path and query.
        """
        metadata_routes = auth_metadata_mount(
            self.issuer,
            enable_multi_zone=self.enable_multi_zone,
            jwks=self.jwks,
            resource_binding=self.resource_binding,
        )
        app.routes.insert(0, metadata_routes)

        app.add_middleware(
            AuthenticationMiddleware,
            backend=KeycardAuthBackend(
                self.get_token_verifier(),
                resource_binding=self.resource_binding,
                issuer=self.issuer,
                zone_resolver=lambda scope: _route_zone(scope, app.routes),
                metadata_matcher=lambda scope: metadata_routes.matches(scope)[0] == Match.FULL,
                introspect=self.introspect,
                credential=self.application_credential,
                introspection_timeout=self.introspection_timeout,
            ),
            on_error=keycard_on_error,
        )

    def grant(
        self,
        resources: str | list[str],
        user_identifier: Callable[..., str] | None = None,
    ):
        """Decorator: delegated OAuth 2.0 token exchange for one or more resources.

        See :func:`keycardai.starlette.authorization.grant` for the full contract.
        """
        return _grant_factory(self, resources, user_identifier=user_identifier)
