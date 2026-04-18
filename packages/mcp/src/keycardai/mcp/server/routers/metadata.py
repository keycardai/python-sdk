"""Route builders for OAuth metadata and protected app mounting.

Re-exported from keycardai.starlette_oauth.routers.metadata for backward compatibility.
Canonical import: ``from keycardai.starlette_oauth.routers import protected_router``
"""

from collections.abc import Sequence

from starlette.routing import Route
from starlette.types import ASGIApp

from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types import JsonWebKeySet
from keycardai.starlette_oauth.routers.metadata import (
    auth_metadata_mount,
    protected_router,
    well_known_authorization_server_route,
    well_known_jwks_route,
    well_known_metadata_mount,
    well_known_metadata_routes,
    well_known_protected_resource_route,
)


def protected_mcp_router(
    issuer: str,
    mcp_app: ASGIApp,
    verifier: TokenVerifier,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
) -> Sequence[Route]:
    """Backward-compatible wrapper that accepts ``mcp_app`` kwarg.

    Delegates to ``protected_router(app=...)`` from keycardai-starlette-oauth.
    """
    return protected_router(
        issuer=issuer,
        app=mcp_app,
        verifier=verifier,
        enable_multi_zone=enable_multi_zone,
        jwks=jwks,
    )


__all__ = [
    "auth_metadata_mount",
    "protected_mcp_router",
    "protected_router",
    "well_known_authorization_server_route",
    "well_known_jwks_route",
    "well_known_metadata_mount",
    "well_known_metadata_routes",
    "well_known_protected_resource_route",
]
