from __future__ import annotations

import logging
from dataclasses import dataclass

from anyio import fail_after
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.routes import build_resource_metadata_url
from pydantic import AnyHttpUrl
from starlette.authentication import AuthenticationError
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response
from starlette.routing import Match, Mount, Route
from starlette.types import Scope

from fastmcp.server.auth.middleware import RequireAuthMiddleware
from keycardai.oauth import AsyncClient
from keycardai.oauth.client_auth import ClientAuthenticator
from keycardai.oauth.exceptions import OAuthError, OAuthHttpError, OAuthProtocolError

try:
    from fastmcp.contrib.component_manager.component_manager import (
        _ComponentManagerAuth,
        _serving_auth,
    )
except ImportError:
    _COMPONENT_MANAGER = None
else:
    _COMPONENT_MANAGER = (_ComponentManagerAuth, _serving_auth)

logger = logging.getLogger(__name__)


class IntrospectionUnavailable(OAuthError):
    pass


class InsufficientScope(Exception):
    pass


@dataclass(frozen=True)
class _ProtectedRoute:
    resource_metadata_url: AnyHttpUrl | None


def _protected_route(scope: Scope) -> _ProtectedRoute | None:
    for route in getattr(scope.get("app"), "routes", ()):
        if route.matches(scope)[0] != Match.FULL:
            continue
        if not isinstance(route, (Route, Mount)):
            return None
        app = route.app
        if isinstance(app, RequireAuthMiddleware):
            return _ProtectedRoute(app.resource_metadata_url)
        if _COMPONENT_MANAGER is not None:
            manager_auth, serving_auth = _COMPONENT_MANAGER
            if isinstance(app, manager_auth):
                auth, mcp_path = serving_auth(scope, app.server)
                if auth is None and app.required_scopes is None:
                    return None
                url = (
                    auth._get_resource_url(mcp_path)
                    if auth is not None and mcp_path is not None
                    else None
                )
                return _ProtectedRoute(build_resource_metadata_url(url) if url else None)
        return None
    return None


def _scope_error(route: _ProtectedRoute) -> JSONResponse:
    challenge = 'Bearer error="insufficient_scope", error_description="Insufficient scope"'
    if route.resource_metadata_url is not None:
        challenge += f', resource_metadata="{route.resource_metadata_url}"'
    return JSONResponse(
        {"error": "insufficient_scope"},
        status_code=403,
        headers={"WWW-Authenticate": challenge},
    )


class TokenIntrospector:
    def __init__(
        self, client: AsyncClient, credential: ClientAuthenticator, timeout: float
    ):
        self._client = client
        self._credential = credential
        self._timeout = timeout
        self._logged_errors: set[tuple[str, int | None, str | None]] = set()

    async def is_active(self, token: str) -> bool:
        try:
            with fail_after(self._timeout):
                result = await self._client.introspect_token(
                    token, credential=self._credential, timeout=self._timeout
                )
            return result.active
        except Exception as exc:
            status = (
                exc.status_code
                if isinstance(exc, (OAuthHttpError, OAuthProtocolError))
                else None
            )
            cause = type(exc.__cause__).__name__ if exc.__cause__ else None
            if isinstance(exc, OAuthProtocolError) and exc.error in {
                "invalid_client", "invalid_request", "invalid_response", "server_error"
            }:
                cause = exc.error
            key = (type(exc).__name__, status, cause)
            if key not in self._logged_errors:
                self._logged_errors.add(key)
                logger.warning(
                    "Token introspection failed: %s (status=%s, cause=%s)", *key
                )
            raise IntrospectionUnavailable("Token introspection unavailable", exc) from exc


class _IntrospectionAuthBackend(BearerAuthBackend):
    async def authenticate(self, conn: HTTPConnection):
        try:
            return await super().authenticate(conn)
        except (IntrospectionUnavailable, InsufficientScope) as exc:
            route = _protected_route(conn.scope)
            if route is None:
                return None
            raise _RequestAuthFailure(route, exc) from exc


class _RequestAuthFailure(AuthenticationError):
    def __init__(
        self, route: _ProtectedRoute, error: IntrospectionUnavailable | InsufficientScope
    ):
        super().__init__(str(error))
        self.response = (
            _scope_error(route) if isinstance(error, InsufficientScope)
            else JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
        )


def _on_auth_error(conn: HTTPConnection, exc: AuthenticationError) -> Response:
    if isinstance(exc, _RequestAuthFailure):
        return exc.response
    return AuthenticationMiddleware.default_on_error(conn, exc)


def introspection_middleware(verifier: TokenVerifier) -> list[Middleware]:
    return [
        Middleware(
            AuthenticationMiddleware,
            backend=_IntrospectionAuthBackend(verifier),
            on_error=_on_auth_error,
        ),
        Middleware(AuthContextMiddleware),
    ]
