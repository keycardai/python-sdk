from collections.abc import Sequence

from starlette.middleware import Middleware
from starlette.routing import Mount, Route
from starlette.types import ASGIApp

from ..auth.verifier import TokenVerifier
from ..handlers.metadata import (
    InferredProtectedResourceMetadata,
    authorization_server_metadata,
    protected_resource_metadata,
)
from ..middleware import BearerAuthMiddleware


def auth_metadata_mount(issuer: str, enable_multi_zone: bool = False) -> Mount:
    return Mount(
        path="/.well-known",
        routes=[
            Route(
                "/oauth-protected-resource{resource_path:path}",
                protected_resource_metadata(
                    InferredProtectedResourceMetadata(
                        authorization_servers=[issuer],
                    ),
                    enable_multi_zone=enable_multi_zone,
                ),
                name="oauth-protected-resource",
            ),
            Route(
                "/oauth-authorization-server{resource_path:path}",
                authorization_server_metadata(
                    issuer, enable_multi_zone=enable_multi_zone
                ),
                name="oauth-authorization-server",
            ),
        ],
        name="well-known",
    )


def protected_mcp_router(
    issuer: str,
    mcp_app: ASGIApp,
    verifier: TokenVerifier,
    enable_multi_zone: bool = False,
) -> Sequence[Route]:
    """Create a protected MCP router with authentication middleware.

    This function creates the routing structure needed for a protected MCP server,
    including OAuth metadata endpoints and the main MCP application with authentication.

    Args:
        issuer: The OAuth issuer URL (zone URL)
        mcp_app: The MCP FastMCP streamable HTTP application
        verifier: Token verifier for authentication middleware
        enable_multi_zone: Whether to enable multi-zone support

    Returns:
        Sequence of routes including metadata mount and protected MCP mount
    """
    routes = [
        auth_metadata_mount(issuer, enable_multi_zone=enable_multi_zone),
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
