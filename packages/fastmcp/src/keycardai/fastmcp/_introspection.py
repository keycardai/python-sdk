from __future__ import annotations

import logging

from anyio import fail_after
from starlette.responses import JSONResponse
from starlette.routing import Match, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from fastmcp.server.auth.middleware import RequireAuthMiddleware
from keycardai.oauth import AsyncClient
from keycardai.oauth.client_auth import ClientAuthenticator
from keycardai.oauth.exceptions import OAuthError, OAuthHttpError, OAuthProtocolError

logger = logging.getLogger(__name__)


class IntrospectionUnavailable(OAuthError):
    pass


class InsufficientScope(Exception):
    pass


def _scope_error(scope: Scope) -> JSONResponse:
    challenge = 'Bearer error="insufficient_scope", error_description="Insufficient scope"'
    for route in getattr(scope.get("app"), "routes", ()):
        if isinstance(route, Route) and route.matches(scope)[0] == Match.FULL:
            if (
                isinstance(route.endpoint, RequireAuthMiddleware)
                and route.endpoint.resource_metadata_url is not None
            ):
                challenge += f', resource_metadata="{route.endpoint.resource_metadata_url}"'
            break
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


class IntrospectionErrorMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except IntrospectionUnavailable:
            response = JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
            await response(scope, receive, send)
        except InsufficientScope:
            await _scope_error(scope)(scope, receive, send)
