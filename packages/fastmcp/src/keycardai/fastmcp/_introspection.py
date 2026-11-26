from __future__ import annotations

import logging

from anyio import fail_after
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
from mcp.server.auth.provider import TokenVerifier
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

logger = logging.getLogger(__name__)


class IntrospectionUnavailable(OAuthError):
    pass


class InsufficientScope(Exception):
    pass


def _required_auth(scope: Scope) -> RequireAuthMiddleware | None:
    for route in getattr(scope.get("app"), "routes", ()):
        if route.matches(scope)[0] != Match.FULL:
            continue
        guard = (
            route.endpoint if isinstance(route, Route)
            else route.app if isinstance(route, Mount)
            else None
        )
        return guard if isinstance(guard, RequireAuthMiddleware) else None
    return None


def _scope_error(guard: RequireAuthMiddleware) -> JSONResponse:
    challenge = 'Bearer error="insufficient_scope", error_description="Insufficient scope"'
    if guard.resource_metadata_url is not None:
        challenge += f', resource_metadata="{guard.resource_metadata_url}"'
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
            guard = _required_auth(conn.scope)
            if guard is None:
                return None
            raise _RequestAuthFailure(guard, exc) from exc


class _RequestAuthFailure(AuthenticationError):
    def __init__(
        self, guard: RequireAuthMiddleware, error: IntrospectionUnavailable | InsufficientScope
    ):
        super().__init__(str(error))
        self.response = (
            _scope_error(guard) if isinstance(error, InsufficientScope)
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
