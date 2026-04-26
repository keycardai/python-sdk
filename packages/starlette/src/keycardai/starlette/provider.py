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
from collections.abc import Callable
from typing import Any

from pydantic import AnyHttpUrl

from keycardai.oauth import AsyncClient, ClientConfig
from keycardai.oauth.http.auth import MultiZoneBasicAuth, NoneAuth
from keycardai.oauth.server.client_factory import ClientFactory, DefaultClientFactory
from keycardai.oauth.server.credentials import (
    ApplicationCredential,
    ClientSecret,
    EKSWorkloadIdentity,
    WebIdentity,
)
from keycardai.oauth.server.exceptions import AuthProviderConfigurationError
from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types.models import JsonWebKeySet
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from .authorization import grant as _grant_factory, requires as _requires_module_func
from .middleware.bearer import KeycardAuthBackend, keycard_on_error
from .routers.metadata import auth_metadata_mount


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
        self._init_lock = asyncio.Lock()
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
        self, auth_info: dict[str, str]
    ) -> AsyncClient | None:
        client = None
        client_key = self._get_client_key(auth_info["zone_id"])
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

    def install(self, app: Any) -> None:
        """Install OAuth metadata discovery routes and AuthenticationMiddleware.

        Adds the ``/.well-known/*`` metadata routes (RFC 9728, RFC 8414, and
        JWKS when configured) and registers
        ``starlette.middleware.authentication.AuthenticationMiddleware`` wired
        to ``KeycardAuthBackend``. After installation, every request passes
        through the backend; routes without ``@requires(...)`` stay public,
        but ``request.user`` / ``request.auth`` are populated for every route.

        Anonymous requests to protected routes receive an RFC 6750 401
        response with a ``WWW-Authenticate: Bearer ... resource_metadata=...``
        header (built by ``keycard_on_error``).
        """
        metadata_routes = auth_metadata_mount(
            self.issuer,
            enable_multi_zone=self.enable_multi_zone,
            jwks=self.jwks,
        )
        app.routes.insert(0, metadata_routes)

        app.add_middleware(
            AuthenticationMiddleware,
            backend=KeycardAuthBackend(self.get_token_verifier()),
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
