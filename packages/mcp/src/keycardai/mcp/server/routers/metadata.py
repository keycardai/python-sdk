from collections.abc import Sequence

from starlette.middleware import Middleware
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from keycardai.oauth.types import JsonWebKeySet

from ..auth.verifier import TokenVerifier
from ..handlers.jwks import jwks_endpoint
from ..handlers.metadata import (
    InferredProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)
from ..middleware import BearerAuthMiddleware


def auth_metadata_mount(
    issuer: str,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
) -> Mount:
    """Create a Starlette Mount for OAuth metadata endpoints at the standard /.well-known path.

    Args:
        issuer: The OAuth issuer URL used for authorization server metadata.
        enable_multi_zone: Whether to enable multi-zone support for metadata endpoints.
            When enabled, metadata responses may include zone-specific information.
        jwks: Optional JSON Web Key Set to expose at the /.well-known/jwks.json endpoint.
            If not provided, no JWKS route will be created.

    Returns:
        A Starlette Mount containing the well-known metadata routes.
    """
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
    """Create a Starlette Mount for OAuth metadata endpoints at a custom path.

    Args:
        issuer: The OAuth issuer URL used for authorization server metadata.
        path: The base path where the mount will be attached (e.g., "/.well-known").
        resource: Optional resource path suffix for metadata routes.
        enable_multi_zone: Whether to enable multi-zone support for metadata endpoints.
        jwks: Optional JSON Web Key Set to expose at the jwks.json endpoint.

    Returns:
        A Starlette Mount containing the well-known metadata routes.
    """
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
    """Create a list of Starlette Routes for OAuth well-known metadata endpoints.

    Args:
        issuer: The OAuth issuer URL used for authorization server metadata.
        enable_multi_zone: Whether to enable multi-zone support for metadata endpoints.
        jwks: Optional JSON Web Key Set to expose. If provided, adds a JWKS route.
        resource: Optional resource path prefix (currently unused in route creation).

    Returns:
        A list of Starlette Route objects for the well-known endpoints.
    """
    routes = [
        well_known_protected_resource_route(issuer, enable_multi_zone),
        well_known_authorization_server_route(issuer, enable_multi_zone),
    ]

    if jwks:
        routes.append(well_known_jwks_route(jwks))

    return routes


def well_known_protected_resource_route(
    issuer: str,
    enable_multi_zone: bool = False,
    resource: str = "/oauth-protected-resource",
) -> Route:
    """Create a Starlette Route for the OAuth Protected Resource Metadata endpoint.

    This endpoint follows RFC 9728 and exposes metadata about the protected resource,
    including which authorization servers can be used to obtain access tokens.

    Args:
        issuer: The OAuth issuer URL, added to the authorization_servers list
            in the protected resource metadata response.
        enable_multi_zone: Whether to enable multi-zone support. When enabled,
            the metadata response may include zone-specific information.
        resource: The path for this route. Defaults to "/oauth-protected-resource"
            as per the well-known URI convention.

    Returns:
        A Starlette Route for the protected resource metadata endpoint.
    """
    inferred_metadata = InferredProtectedResourceMetadata(
        authorization_servers=[issuer],
    )

    return Route(
        resource,
        protected_resource_metadata(
            inferred_metadata,
            enable_multi_zone=enable_multi_zone,
        ),
        name="oauth-protected-resource",
    )


def well_known_authorization_server_route(
    issuer: str,

    enable_multi_zone: bool = False,
    resource: str = "/oauth-authorization-server",
) -> Route:
    """Create a Starlette Route for the OAuth Authorization Server Metadata endpoint.

    This endpoint follows RFC 8414 and exposes metadata about the authorization server,
    enabling clients to discover OAuth endpoints and capabilities dynamically.

    Args:
        issuer: The OAuth issuer URL, used as the issuer identifier in the
            authorization server metadata response.
        enable_multi_zone: Whether to enable multi-zone support. When enabled,
            the metadata response may include zone-specific information.
        resource: The path for this route. Defaults to "/oauth-authorization-server"
            as per the well-known URI convention.

    Returns:
        A Starlette Route for the authorization server metadata endpoint.
    """
    return Route(
        resource,
        authorization_server_metadata(
            issuer,
            enable_multi_zone=enable_multi_zone,
        ),
        name="oauth-authorization-server",
    )


def well_known_jwks_route(jwks: JsonWebKeySet) -> Route:
    """Create a Starlette Route for the JSON Web Key Set (JWKS) endpoint.

    This endpoint exposes the public keys used for token verification,
    allowing clients to validate JWT signatures. The endpoint is typically
    served at /.well-known/jwks.json.

    Args:
        jwks: The JSON Web Key Set containing public keys to expose.
            This should contain the public keys corresponding to the
            private keys used for signing tokens.

    Returns:
        A Starlette Route for the JWKS endpoint at "/jwks.json".
    """
    return Route(
        "/jwks.json",
        jwks_endpoint(jwks),
        name="jwks",
    )


def protected_mcp_router(
    issuer: str,
    mcp_app: ASGIApp,
    verifier: TokenVerifier,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
) -> Sequence[Route]:
    """Create a protected MCP router with authentication middleware.

    This function creates the complete routing structure needed for a protected
    MCP server, including OAuth metadata endpoints and the main MCP application
    wrapped with bearer token authentication middleware.

    The router includes:
    - OAuth well-known metadata endpoints (protected resource, authorization server)
    - Optional JWKS endpoint for token verification
    - The MCP application protected by BearerAuthMiddleware

    Args:
        issuer: The OAuth issuer URL (zone URL) used for metadata endpoints.
        mcp_app: The MCP application (typically a FastMCP streamable HTTP app)
            to be protected with authentication.
        verifier: Token verifier instance used by the authentication middleware
            to validate incoming bearer tokens.
        enable_multi_zone: Whether to enable multi-zone support. When True,
            the MCP app is mounted at "/{zone_id:str}" instead of "/".
        jwks: Optional JSON Web Key Set to expose at the JWKS endpoint.
            If provided, clients can fetch public keys for token verification.

    Returns:
        A sequence of routes including the metadata mount and the protected
        MCP application mount.
    """
    routes = [
        auth_metadata_mount(issuer, enable_multi_zone=enable_multi_zone, jwks=jwks),
    ]

    if enable_multi_zone:
        # Multi-zone route with zone_id path parameter
        routes.append(
            Mount(
                "/{zone_id:str}",
                app=mcp_app,
                middleware=[Middleware(BearerAuthMiddleware, verifier)],
            )
        )
    else:
        # Single zone route mounted at root
        routes.append(
            Mount(
                "/",
                app=mcp_app,
                middleware=[Middleware(BearerAuthMiddleware, verifier)],
            )
        )

    return routes
