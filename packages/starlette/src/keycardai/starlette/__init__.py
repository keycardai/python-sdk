"""Keycard Starlette OAuth: protect HTTP APIs with Keycard.

Plugs Keycard bearer-token authentication into Starlette's standard
authentication framework: ``AuthenticationMiddleware`` populates
``request.user`` / ``request.auth`` via :class:`KeycardAuthBackend`, the
:func:`requires` decorator gates routes (drop-in for
``starlette.authentication.requires`` with RFC 6750 challenges), and
:func:`grant` (also exposed as ``AuthProvider.grant``) performs delegated
OAuth 2.0 token exchange (RFC 8693) for downstream APIs.

Quick Start::

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

    # Each discoverable route needs its own binding; /api/data has no discovery.
    @app.get("/api/data")
    @requires("authenticated")
    @auth.grant("https://api.example.com")
    async def get_data(request: Request, access: AccessContext):
        token = access.access("https://api.example.com").access_token

Use ``allow_any_audience=True`` to explicitly skip audience verification.
"""

from .authorization import grant, requires
from .binding import (
    LegacyResourceBindingWarning,
    ResourceBinding,
    ResourceBindingConfigError,
)
from .handlers.metadata import ProtectedResourceMetadata
from .middleware import (
    KeycardAuthBackend,
    KeycardAuthCredentials,
    KeycardAuthError,
    KeycardUser,
    keycard_on_error,
)
from .provider import AuthProvider
from .routers import (
    auth_metadata_mount,
    protected_router,
    well_known_metadata_mount,
    well_known_metadata_routes,
)

__all__ = [
    # === Primary API ===
    "AuthProvider",
    "LegacyResourceBindingWarning",
    "ResourceBinding",
    "ResourceBindingConfigError",
    "requires",
    "grant",
    # === Authentication backend ===
    "KeycardAuthBackend",
    "KeycardAuthCredentials",
    "KeycardAuthError",
    "KeycardUser",
    "keycard_on_error",
    # === Route Builders ===
    "auth_metadata_mount",
    "protected_router",
    "well_known_metadata_mount",
    "well_known_metadata_routes",
    # === Models ===
    "ProtectedResourceMetadata",
]
