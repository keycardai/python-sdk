"""Starlette route builders for OAuth metadata and protected app mounting.

Provides composable route builders for:
- OAuth Protected Resource Metadata (RFC 9728)
- OAuth Authorization Server Metadata (RFC 8414)
- JWKS endpoint
- Bearer-authenticated app mounting
"""

from collections.abc import Sequence
from re import compile as compile_pattern, escape
from urllib.parse import unquote, urlsplit

from keycardai.oauth.server.verifier import TokenVerifier
from keycardai.oauth.types import JsonWebKeySet
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import BaseRoute, Match, Mount, Route
from starlette.types import ASGIApp, Scope

from ..binding import (
    PROTECTED_RESOURCE_PATH,
    Audience,
    ResourceBinding,
    ResourceBindingConfigError,
    metadata_url,
    resolve_binding,
)
from ..handlers.jwks import jwks_endpoint
from ..handlers.metadata import (
    ProtectedResourceMetadata,
    _protected_resource_metadata,
    authorization_server_metadata,
)
from ..middleware import KeycardAuthBackend, keycard_on_error


class _ProtectedResourceRoute(Route):
    def __init__(self, path: str, endpoint, identifier: str) -> None:
        super().__init__(
            "/oauth-protected-resource",
            endpoint,
            name="oauth-protected-resource",
            methods=["GET", "OPTIONS"],
        )
        self.path = self.path_format = unquote(path)
        self.path_regex = compile_pattern(f"^{escape(self.path)}\\Z")
        self.raw_path = urlsplit(metadata_url(identifier)).path.encode()
        self.query = urlsplit(identifier).query.encode()

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        raw_path = scope.get("raw_path")
        if scope.get("query_string", b"") != self.query or (
            raw_path is not None and not raw_path.endswith(self.raw_path)
        ):
            return Match.NONE, {}
        return super().matches(scope)


class _ZoneAuthorizationServerRoute(Route):
    def __init__(self, path: str, endpoint, zones: set[str]) -> None:
        super().__init__(
            path, endpoint, name="oauth-authorization-server", methods=["GET", "OPTIONS"]
        )
        self.zones = zones

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if child_scope.get("path_params", {}).get("zone_id") not in self.zones:
            return Match.NONE, {}
        return match, child_scope


class _ExplicitMetadataMount(Mount):
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        match, child_scope = super().matches(scope)
        if match == Match.FULL and not any(
            route.matches({**scope, **child_scope})[0] != Match.NONE
            for route in self.routes
        ):
            return Match.NONE, {}
        return match, child_scope


def auth_metadata_mount(
    issuer: str,
    enable_multi_zone: bool = False,
    jwks: JsonWebKeySet | None = None,
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    as_metadata_timeout: float = 10.0,
    *,
    resource_identifier: str | None = None,
    resource_identifiers_by_zone: dict[str, str] | None = None,
    multi_resource: bool = False,
    audience: Audience = None,
    allow_any_audience: bool = False,
    resource_binding: ResourceBinding | None = None,
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
        resource_binding: Reuse a provider's binding, or pass the explicit
            resource mode options directly.
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
        resource_identifier=resource_identifier,
        resource_identifiers_by_zone=resource_identifiers_by_zone,
        multi_resource=multi_resource,
        audience=audience,
        allow_any_audience=allow_any_audience,
        resource_binding=resource_binding,
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
    *,
    resource_identifier: str | None = None,
    resource_identifiers_by_zone: dict[str, str] | None = None,
    multi_resource: bool = False,
    audience: Audience = None,
    allow_any_audience: bool = False,
    resource_binding: ResourceBinding | None = None,
) -> Mount:
    """Create a Starlette Mount for OAuth metadata endpoints at a custom path."""
    binding = resolve_binding(
        resource_binding,
        resource_identifier=resource_identifier,
        resource_identifiers_by_zone=resource_identifiers_by_zone,
        multi_resource=multi_resource,
        audience=audience,
        allow_any_audience=allow_any_audience,
        metadata_only=True,
    )
    mount = Mount if binding.mode == "legacy" else _ExplicitMetadataMount
    return mount(
        path=path,
        routes=_metadata_routes(
            issuer=issuer,
            enable_multi_zone=enable_multi_zone,
            jwks=jwks,
            resource=resource,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
            as_metadata_timeout=as_metadata_timeout,
            binding=binding,
            mount_path=path.rstrip("/"),
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
    *,
    resource_identifier: str | None = None,
    resource_identifiers_by_zone: dict[str, str] | None = None,
    multi_resource: bool = False,
    audience: Audience = None,
    allow_any_audience: bool = False,
    resource_binding: ResourceBinding | None = None,
) -> list[Route]:
    """Create metadata routes; explicit discovery routes are rooted at /.well-known."""
    binding = resolve_binding(
        resource_binding,
        resource_identifier=resource_identifier,
        resource_identifiers_by_zone=resource_identifiers_by_zone,
        multi_resource=multi_resource,
        audience=audience,
        allow_any_audience=allow_any_audience,
        metadata_only=True,
    )
    return _metadata_routes(
        issuer=issuer,
        enable_multi_zone=enable_multi_zone,
        jwks=jwks,
        resource=resource,
        scopes_supported=scopes_supported,
        resource_name=resource_name,
        resource_documentation=resource_documentation,
        as_metadata_timeout=as_metadata_timeout,
        binding=binding,
        mount_path="",
    )


def _metadata_routes(
    *,
    issuer: str,
    enable_multi_zone: bool,
    jwks: JsonWebKeySet | None,
    resource: str,
    scopes_supported: list[str] | None,
    resource_name: str | None,
    resource_documentation: str | None,
    as_metadata_timeout: float,
    binding: ResourceBinding,
    mount_path: str,
    include_jwks_uri: bool | None = None,
) -> list[Route]:
    if include_jwks_uri is None:
        include_jwks_uri = jwks is not None
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

    def make_metadata(issuers: list[str]) -> ProtectedResourceMetadata:
        return ProtectedResourceMetadata(
            authorization_servers=issuers,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
        )

    routes: list[Route] = []
    zones = binding.zone_issuers(issuer)
    jwks_path = "/jwks.json"
    if binding.mode == "legacy":
        routes.append(Route(
            protected_resource_path,
            _protected_resource_metadata(
                make_metadata([issuer]),
                enable_multi_zone=enable_multi_zone,
                include_jwks_uri=include_jwks_uri,
            ),
            name="oauth-protected-resource",
            methods=["GET", "OPTIONS"],
        ))
    else:
        if not mount_path:
            auth_server_path = f"/.well-known{auth_server_path}"
            jwks_path = f"/.well-known{jwks_path}"
        if binding.mode in {"per_zone", "shared"}:
            auth_server_path = f"{'/.well-known' if not mount_path else ''}/oauth-authorization-server/{{zone_id:str}}"

        for identifier, issuers in binding.documents(issuer):
            url = urlsplit(metadata_url(identifier))
            if mount_path not in {"", "/.well-known"}:
                raise ResourceBindingConfigError(
                    f"Protected-resource metadata must be registered at {PROTECTED_RESOURCE_PATH}"
                )
            routes.append(_ProtectedResourceRoute(
                url.path[len(mount_path):],
                _protected_resource_metadata(
                    make_metadata(issuers),
                    include_jwks_uri=include_jwks_uri,
                    resource_identifier=identifier,
                    authorization_servers=issuers,
                ),
                identifier,
            ))

    if issuer or binding.mode == "legacy":
        zoned = binding.mode in {"per_zone", "shared"}
        proxy = authorization_server_metadata(
            issuer,
            enable_multi_zone=enable_multi_zone,
            timeout=as_metadata_timeout,
            zone_issuers=zones if zoned else None,
        )
        routes.append(
            _ZoneAuthorizationServerRoute(auth_server_path, proxy, set(zones))
            if zoned else Route(
                auth_server_path,
                proxy,
                name="oauth-authorization-server",
                methods=["GET", "OPTIONS"],
            )
        )

    if jwks:
        routes.append(
            Route(
                jwks_path,
                jwks_endpoint(jwks),
                name="jwks",
                methods=["GET", "OPTIONS"],
            )
        )

    return routes


def well_known_protected_resource_route(
    issuer: str,
    enable_multi_zone: bool = False,
    resource: str | None = None,
    scopes_supported: list[str] | None = None,
    resource_name: str | None = None,
    resource_documentation: str | None = None,
    include_jwks_uri: bool = True,
    *,
    resource_identifier: str | None = None,
    resource_identifiers_by_zone: dict[str, str] | None = None,
    multi_resource: bool = False,
    audience: Audience = None,
    allow_any_audience: bool = False,
    resource_binding: ResourceBinding | None = None,
) -> Route | None:
    """Create a Starlette Route for the OAuth Protected Resource Metadata endpoint (RFC 9728)."""
    binding = resolve_binding(
        resource_binding,
        resource_identifier=resource_identifier,
        resource_identifiers_by_zone=resource_identifiers_by_zone,
        multi_resource=multi_resource,
        audience=audience,
        allow_any_audience=allow_any_audience,
        metadata_only=True,
    )
    if binding.mode != "legacy":
        routes = _metadata_routes(
            issuer=issuer,
            enable_multi_zone=enable_multi_zone,
            jwks=None,
            resource="",
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
            as_metadata_timeout=10.0,
            binding=binding,
            mount_path="",
            include_jwks_uri=include_jwks_uri,
        )
        documents = [route for route in routes if isinstance(route, _ProtectedResourceRoute)]
        if len(documents) > 1:
            raise ResourceBindingConfigError("Use well_known_metadata_routes for multiple documents")
        if not documents:
            return None
        route = documents[0]
        if resource is not None and resource != route.path:
            raise ResourceBindingConfigError("Protected-resource metadata path does not match")
        return route
    metadata = ProtectedResourceMetadata(
        authorization_servers=[issuer],
        scopes_supported=scopes_supported,
        resource_name=resource_name,
        resource_documentation=resource_documentation,
    )
    return Route(
        "/oauth-protected-resource" if resource is None else resource,
        _protected_resource_metadata(
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
    *,
    resource_identifier: str | None = None,
    resource_identifiers_by_zone: dict[str, str] | None = None,
    multi_resource: bool = False,
    audience: Audience = None,
    allow_any_audience: bool = False,
    resource_binding: ResourceBinding | None = None,
) -> Sequence[BaseRoute]:
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

        verifier = TokenVerifier(
            issuer="https://zone.keycard.cloud",
            audience="https://api.example.com",
        )
        app = Starlette(routes=protected_router(
            issuer="https://zone.keycard.cloud",
            app=my_asgi_app,
            verifier=verifier,
            resource_identifier="https://api.example.com",
        ))
    """
    backend = KeycardAuthBackend(
        verifier,
        require_authentication=require_authentication,
        resource_identifier=resource_identifier,
        resource_identifiers_by_zone=resource_identifiers_by_zone,
        multi_resource=multi_resource,
        audience=audience,
        allow_any_audience=allow_any_audience,
        resource_binding=resource_binding,
        issuer=issuer,
    )
    binding = backend.resource_binding
    binding._validate_multi_zone(enable_multi_zone)
    if binding.mode == "per_zone" and not enable_multi_zone:
        raise ResourceBindingConfigError("Per-zone binding requires a zone mount")
    if binding.mode == "shared" and enable_multi_zone:
        raise ResourceBindingConfigError("Shared discovery requires token-keyed routing")
    routes: list[Mount | Route] = [
        auth_metadata_mount(
            issuer,
            enable_multi_zone=enable_multi_zone,
            jwks=jwks,
            scopes_supported=scopes_supported,
            resource_name=resource_name,
            resource_documentation=resource_documentation,
            as_metadata_timeout=as_metadata_timeout,
            resource_binding=binding,
        ),
    ]

    auth_middleware = Middleware(
        AuthenticationMiddleware,
        backend=backend,
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
    if not enable_multi_zone or binding.mode == "per_zone":
        routes.append(
            Mount(
                "/",
                app=app,
                middleware=[auth_middleware],
            )
        )

    return routes
