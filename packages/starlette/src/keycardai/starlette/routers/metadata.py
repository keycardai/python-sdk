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
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    as_metadata_timeout: float = 10.0,
) -> Mount:
    """Create a Starlette Mount for OAuth metadata endpoints at ``/.well-known``.

    Args:
        issuer: OAuth issuer URL for metadata endpoints.
        enable_multi_zone: When True, derive a zone-scoped authorization
            server from the request path.
        jwks: Optional JWKS to expose at ``/.well-known/jwks.json``.
        scopes_supported: Optional scope values advertised in the protected
            resource metadata document.
        resource_name: Optional human-readable resource name advertised in
            the protected resource metadata document.
        resource_documentation: Optional documentation URL advertised in the
            protected resource metadata document.
        as_metadata_timeout: Timeout in seconds for the upstream
            authorization server metadata fetch.
    """
    return well_known_metadata_mount(
        path="/.well-known",
        issuer=issuer,
        resource="{resource_path:path}",
        enable_multi_zone=enable_multi_zone,
        jwks=jwks,
        scopes_supported=scopes_supported,
        resource_name=resource_name,
        resource_documentation=resource_documentation,
        as_metadata_timeout=as_metadata_timeout,
    )


def well_known_metadata_mount(
    issuer: str,
    path: str,
    resource: str = "",
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    as_metadata_timeout: float = 10.0,
) -> Mount:
    """Create a Starlette Mount for OAuth metadata endpoints at a custom path."""
    return Mount(
        path=path,
        routes=well_known_metadata_routes(
            issuer=issuer,
            enable_multi_zone=enable_multi_zone,
            jwks=jwks,
            resource=resource,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
            as_metadata_timeout=as_metadata_timeout,
        ),
    )


def well_known_metadata_routes(
    issuer: str,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
    resource: str = "",
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    as_metadata_timeout: float = 10.0,
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
        scopes_supported=scopes_supported,
        resource_name=resource_name,
        resource_documentation=resource_documentation,
    )

    routes = [
        Route(
            protected_resource_path,
            protected_resource_metadata(
                metadata,
                enable_multi_zone=enable_multi_zone,
                include_jwks_uri=jwks is not None,
            ),
            name="oauth-protected-resource",
            methods=["GET", "OPTIONS"],
        ),
        Route(
            auth_server_path,
            authorization_server_metadata(
                issuer,
                enable_multi_zone=enable_multi_zone,
                timeout=as_metadata_timeout,
            ),
            name="oauth-authorization-server",
            methods=["GET", "OPTIONS"],
        ),
    ]

    if jwks:
        routes.append(
            Route(
                "/jwks.json",
                jwks_endpoint(jwks),
                name="jwks",
                methods=["GET", "OPTIONS"],
            )
        )

    return routes


def well_known_protected_resource_route(
    issuer: str,
    enable_multi_zone: bool = False,
    resource: str = "/oauth-protected-resource",
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    include_jwks_uri: bool = True,
) -> Route:
    """Create a Starlette Route for the OAuth Protected Resource Metadata endpoint (RFC 9728)."""
    metadata = ProtectedResourceMetadata(
        authorization_servers=[issuer],
        scopes_supported=scopes_supported,
        resource_name=resource_name,
        resource_documentation=resource_documentation,
    )
    return Route(
        resource,
        protected_resource_metadata(
            metadata,
            enable_multi_zone=enable_multi_zone,
            include_jwks_uri=include_jwks_uri,
        ),
        name="oauth-protected-resource",
        methods=["GET", "OPTIONS"],
    )


def well_known_authorization_server_route(
    issuer: str,
    enable_multi_zone: bool = False,
    resource: str = "/oauth-authorization-server",
    as_metadata_timeout: float = 10.0,
) -> Route:
    """Create a Starlette Route for the OAuth Authorization Server Metadata endpoint (RFC 8414)."""
    return Route(
        resource,
        authorization_server_metadata(
            issuer,
            enable_multi_zone=enable_multi_zone,
            timeout=as_metadata_timeout,
        ),
        name="oauth-authorization-server",
        methods=["GET", "OPTIONS"],
    )


def well_known_jwks_route(jwks: JsonWebKeySet) -> Route:
    """Create a Starlette Route for the JSON Web Key Set (JWKS) endpoint."""
    return Route(
        "/jwks.json", jwks_endpoint(jwks), name="jwks", methods=["GET", "OPTIONS"]
    )


def protected_router(
    issuer: str,
    app: ASGIApp,
    verifier: TokenVerifier,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
    require_authentication: bool = False,
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    as_metadata_timeout: float = 10.0,
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
        require_authentication: When True, a request to the mounted app without
            an ``Authorization`` header is rejected with an RFC 6750 challenge
            instead of falling through anonymously. Use this for opaque ASGI
            sub-apps (e.g. an MCP JSONRPC dispatcher) that have no per-route
            ``@requires("authenticated")`` gate of their own. Defaults to False
            to preserve the mixed-route behavior.
        scopes_supported: Optional scope values advertised in the protected
            resource metadata document.
        resource_name: Optional human-readable resource name advertised in
            the protected resource metadata document.
        resource_documentation: Optional documentation URL advertised in the
            protected resource metadata document.
        as_metadata_timeout: Timeout in seconds for the upstream
            authorization server metadata fetch.

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
            issuer,
            enable_multi_zone=enable_multi_zone,
            jwks=jwks,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
            as_metadata_timeout=as_metadata_timeout,
        ),
    ]

    auth_middleware = Middleware(
        AuthenticationMiddleware,
        backend=KeycardAuthBackend(
            verifier, require_authentication=require_authentication
        ),
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
