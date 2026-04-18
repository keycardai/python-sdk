"""Keycard Starlette OAuth — protect any HTTP API with Keycard.

Starlette/FastAPI middleware, route builders, and a @protect() decorator
for OAuth 2.0 bearer token authentication. No MCP dependency.

Quick Start::

    from fastapi import FastAPI, Request
    from keycardai.starlette_oauth import AuthProvider
    from keycardai.oauth.server import AccessContext, ClientSecret

    auth = AuthProvider(
        zone_id="your-zone-id",
        application_credential=ClientSecret(("client_id", "client_secret")),
    )

    app = FastAPI()
    auth.install(app)

    @app.get("/api/data")
    @auth.protect("https://api.example.com")
    async def get_data(request: Request, access: AccessContext):
        token = access.access("https://api.example.com").access_token
        # Use token to call downstream API
"""

from .handlers.metadata import ProtectedResourceMetadata
from .middleware import BearerAuthMiddleware
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
    # === Middleware ===
    "BearerAuthMiddleware",
    # === Route Builders ===
    "auth_metadata_mount",
    "protected_router",
    "well_known_metadata_mount",
    "well_known_metadata_routes",
    # === Models ===
    "ProtectedResourceMetadata",
]
