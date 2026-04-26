"""Starlette route builders for OAuth metadata and protected app mounting.

Provides composable route builders for:
- OAuth Protected Resource Metadata (RFC 9728)
- OAuth Authorization Server Metadata (RFC 8414)
- JWKS endpoint
- Bearer-authenticated app mounting
"""

from collections.abc import Sequence

from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types import JsonWebKeySet
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from ..handlers.jwks import jwks_endpoint
from ..handlers.metadata import (
    ProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)
from ..middleware import KeycardAuthBackend, keycard_on_error


def auth_metadata_mount(
    issuer: str,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
) -> Mount:
    """Create a Starlette Mount for OAuth metadata endpoints at ``/.well-known``."""
    return well_known_metadata_mount(
        path="/.well-known",
        issuer=issuer,
        resource="{resource_path:path}",
        enable_multi_zone=enable_multi_zone,
        jwks=jwks,
    )


def well_known_metadata_mount(
    issuer: str,
    path: str,
    resource: str = "",
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
) -> Mount:
    """Create a Starlette Mount for OAuth metadata endpoints at a custom path."""
    return Mount(
        path=path,
        routes=well_known_metadata_routes(
            issuer=issuer,
            enable_multi_zone=enable_multi_zone,
            jwks=jwks,
            resource=resource,
        ),
    )


def well_known_metadata_routes(
    issuer: str,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
    resource: str = "",
) -> list[Route]:
    """Create Starlette Routes for OAuth well-known metadata endpoints."""
    protected_resource_path = (
        f"/oauth-protected-resource{resource}"
        if resource
        else "/oauth-protected-resource"
    )
    auth_server_path = (
        f"/oauth-authorization-server{resource}"
        if resource
        else "/oauth-authorization-server"
    )

    metadata = ProtectedResourceMetadata(
        authorization_servers=[issuer],
    )

    routes = [
        Route(
            protected_resource_path,
            protected_resource_metadata(
                metadata, enable_multi_zone=enable_multi_zone
            ),
            name="oauth-protected-resource",
        ),
        Route(
            auth_server_path,
            authorization_server_metadata(
                issuer, enable_multi_zone=enable_multi_zone
            ),
            name="oauth-authorization-server",
        ),
    ]

    if jwks:
        routes.append(
            Route("/jwks.json", jwks_endpoint(jwks), name="jwks")
        )

    return routes


def well_known_protected_resource_route(
    issuer: str,
    enable_multi_zone: bool = False,
    resource: str = "/oauth-protected-resource",
) -> Route:
    """Create a Starlette Route for the OAuth Protected Resource Metadata endpoint (RFC 9728)."""
    metadata = ProtectedResourceMetadata(
        authorization_servers=[issuer],
    )
    return Route(
        resource,
        protected_resource_metadata(
            metadata, enable_multi_zone=enable_multi_zone
        ),
        name="oauth-protected-resource",
    )


def well_known_authorization_server_route(
    issuer: str,
    enable_multi_zone: bool = False,
    resource: str = "/oauth-authorization-server",
) -> Route:
    """Create a Starlette Route for the OAuth Authorization Server Metadata endpoint (RFC 8414)."""
    return Route(
        resource,
        authorization_server_metadata(
            issuer, enable_multi_zone=enable_multi_zone
        ),
        name="oauth-authorization-server",
    )


def well_known_jwks_route(jwks: JsonWebKeySet) -> Route:
    """Create a Starlette Route for the JSON Web Key Set (JWKS) endpoint."""
    return Route("/jwks.json", jwks_endpoint(jwks), name="jwks")


def protected_router(
    issuer: str,
    app: ASGIApp,
    verifier: TokenVerifier,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
) -> Sequence[Route]:
    """Create a protected router with OAuth metadata and bearer auth middleware.

    Wraps any ASGI application with bearer token authentication and adds
    OAuth discovery endpoints. This is the protocol-agnostic equivalent of
    MCP's ``protected_mcp_router``.

    Args:
        issuer: OAuth issuer URL for metadata endpoints.
        app: The ASGI application to protect with authentication.
        verifier: Token verifier for bearer token validation.
        enable_multi_zone: When True, mount the app at ``/{zone_id:str}``.
        jwks: Optional JWKS to expose at ``/.well-known/jwks.json``.

    Returns:
        Sequence of routes including metadata mount and protected app mount.

    Example::

        from starlette.applications import Starlette
        from keycardai.starlette import protected_router
        from keycardai.oauth.server import TokenVerifier

        verifier = TokenVerifier(issuer="https://zone.keycard.cloud")
        app = Starlette(routes=protected_router(
            issuer="https://zone.keycard.cloud",
            app=my_asgi_app,
            verifier=verifier,
        ))
    """
    routes: list[Mount | Route] = [
        auth_metadata_mount(
            issuer, enable_multi_zone=enable_multi_zone, jwks=jwks
        ),
    ]

    auth_middleware = Middleware(
        AuthenticationMiddleware,
        backend=KeycardAuthBackend(verifier),
        on_error=keycard_on_error,
    )

    if enable_multi_zone:
        routes.append(
            Mount(
                "/{zone_id:str}",
                app=app,
                middleware=[auth_middleware],
            )
        )
    else:
        routes.append(
            Mount(
                "/",
                app=app,
                middleware=[auth_middleware],
            )
        )

    return routes
