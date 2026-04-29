"""Standard Starlette authentication backend for Keycard bearer tokens.

This module exposes two layers:

1. The current API (used by ``AuthProvider.install``):
   ``KeycardAuthBackend`` (a standard ``AuthenticationBackend``) that verifies
   incoming bearer tokens via a ``TokenVerifier`` and populates
   ``request.user`` (a ``KeycardUser``) and ``request.auth`` (a
   ``KeycardAuthCredentials``). The on-error hook (``keycard_on_error``)
   maps a ``KeycardAuthError`` raised by the backend into an RFC 6750
   ``WWW-Authenticate`` challenge that includes the ``resource_metadata=``
   URL required by RFC 9728.

2. Deprecated legacy symbols (``BearerAuthMiddleware``, ``verify_bearer_token``,
   ``_create_auth_challenge_response``) preserved for downstream packages
   (``keycardai-mcp``, ``keycardai-agents``) until those callers migrate to
   ``AuthenticationMiddleware(backend=KeycardAuthBackend(...),
   on_error=keycard_on_error)``. They will be removed once the migration
   is complete; do not use them in new code.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence

from pydantic import AnyHttpUrl

from keycardai.oauth.server.verifier import TokenVerifier
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection, Request
from starlette.responses import Response
from starlette.types import ASGIApp

from ..shared.starlette import get_base_url

# OAuth metadata discovery endpoints that must remain publicly reachable per
# RFC 9728 §2 and RFC 8414 §3. Other entries under /.well-known/ (change-password,
# assetlinks.json, etc.) are NOT exempt and stay behind the bearer check.
_OAUTH_METADATA_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-authorization-server",
    "/.well-known/jwks.json",
)


def _is_oauth_metadata_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _OAUTH_METADATA_PATHS)


def _get_oauth_protected_resource_url(conn: HTTPConnection | Request) -> str:
    path = conn.url.path.lstrip("/").rstrip("/")
    # ``get_base_url`` accepts a Starlette Request; HTTPConnection exposes the
    # same scope/headers/url surface we use, so a Request wrapper works in both
    # the middleware and on_error code paths.
    base_url = get_base_url(conn if isinstance(conn, Request) else Request(conn.scope))
    return str(AnyHttpUrl(f"{base_url}/.well-known/oauth-protected-resource/{path}"))


def _get_bearer_token(conn: HTTPConnection | Request) -> str | None:
    """Extract the bearer token from a request's Authorization header.

    Returns ``None`` for a missing header, an empty header, a non-Bearer scheme,
    or a malformed value with anything other than two whitespace-separated
    parts. Returns the empty string when the header is exactly ``"Bearer "``
    (Bearer scheme followed by a single space and an empty token).
    """
    header = conn.headers.get("Authorization")
    if header is None or len(header) == 0:
        return None
    parts = header.split(" ")
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1]


def _build_challenge_header(error: str, description: str, resource_metadata: str) -> str:
    return (
        f'Bearer error="{error}", '
        f'error_description="{description}", '
        f'resource_metadata="{resource_metadata}"'
    )


def _create_auth_challenge_response(
    error: str,
    description: str,
    request: Request,
    status_code: int = 401,
) -> Response:
    """Create a standardized OAuth 2.0 Bearer challenge response (RFC 6750).

    .. deprecated::
        Kept for ``BearerAuthMiddleware`` and downstream callers. New code
        should rely on ``keycard_on_error`` together with
        ``KeycardAuthBackend``.
    """
    response = Response(
        content="Unauthorized" if status_code == 401 else "Forbidden",
        status_code=status_code,
    )
    response.headers["WWW-Authenticate"] = _build_challenge_header(
        error, description, _get_oauth_protected_resource_url(request)
    )
    return response


class KeycardAuthError(AuthenticationError):
    """AuthenticationError carrying the OAuth ``error`` code and HTTP status.

    ``starlette.middleware.authentication.AuthenticationMiddleware`` invokes
    ``on_error`` with this instance; ``keycard_on_error`` reads ``error`` and
    ``status_code`` from it to build an RFC 6750 challenge response.
    """

    def __init__(self, error: str, description: str, *, status_code: int = 401):
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


class KeycardUser(BaseUser):
    """Authenticated Keycard user backed by a verified access token.

    Surfaces the standard Starlette ``BaseUser`` interface plus Keycard
    specifics (``access_token``, ``zone_id``) needed by ``@auth.grant()``
    for delegated token exchange.
    """

    def __init__(
        self,
        *,
        access_token: str,
        client_id: str,
        zone_id: str | None,
        resource_server_url: str,
        scopes: Sequence[str] | None = None,
    ):
        self.access_token = access_token
        self.client_id = client_id
        self.zone_id = zone_id
        self.resource_server_url = resource_server_url
        self.scopes = list(scopes or [])

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self.client_id

    @property
    def identity(self) -> str:
        return self.client_id


class KeycardAuthCredentials(AuthCredentials):
    """AuthCredentials that always include the synthetic ``"authenticated"`` scope.

    Mirrors the convention used in the Starlette docs (``@requires("authenticated")``)
    so downstream gating is purely scope-based.
    """

    def __init__(self, scopes: Sequence[str] | None = None):
        scope_set = list(scopes or [])
        if "authenticated" not in scope_set:
            scope_set.append("authenticated")
        super().__init__(scope_set)


class KeycardAuthBackend(AuthenticationBackend):
    """Standard ``AuthenticationBackend`` that verifies Keycard bearer tokens.

    Behavior contract:

    - Requests to OAuth metadata paths (``/.well-known/oauth-*``,
      ``/.well-known/jwks.json``) always pass through anonymously per
      RFC 9728 §2 and RFC 8414 §3.
    - No ``Authorization`` header on a non-metadata path:

      - Default (``require_authentication=False``): returns ``None`` so the
        request stays anonymous (``request.user = UnauthenticatedUser()``).
        Public routes remain reachable; protected routes that are gated by
        ``@requires("authenticated")`` 401 at the decorator boundary. Use
        this for mixed-route apps.
      - ``require_authentication=True``: raises ``KeycardAuthError`` so the
        middleware invokes ``on_error`` immediately. Use this on mounts
        where every path requires auth and there is no per-route gate
        downstream (for example, a JSONRPC dispatcher mount or any
        non-Starlette ASGI sub-app).

    - Malformed ``Authorization`` header, missing zone id under multi-zone
      configuration, or token verification failure → raises
      ``KeycardAuthError`` so the middleware invokes ``on_error``.
    - Valid token → returns ``(KeycardAuthCredentials, KeycardUser)``.

    Args:
        verifier: TokenVerifier instance configured for the zone.
        require_authentication: When True, missing Authorization on a
            non-metadata path raises ``KeycardAuthError`` instead of
            falling through anonymously. Defaults to False to preserve
            the mixed-route default.
    """

    def __init__(
        self,
        verifier: TokenVerifier,
        *,
        require_authentication: bool = False,
    ):
        self.verifier = verifier
        self.require_authentication = require_authentication

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[KeycardAuthCredentials, KeycardUser] | None:
        if _is_oauth_metadata_path(conn.url.path):
            return None

        if not conn.headers.get("Authorization"):
            if self.require_authentication:
                raise KeycardAuthError(
                    "invalid_token", "No bearer token provided"
                )
            return None

        token = _get_bearer_token(conn)
        if token is None:
            raise KeycardAuthError(
                "invalid_token",
                "Invalid Authorization header format",
                status_code=400,
            )

        zone_id: str | None = None
        if self.verifier.enable_multi_zone:
            zone_id = conn.path_params.get("zone_id") if hasattr(conn, "path_params") else None
            if zone_id is None:
                raise KeycardAuthError("invalid_token", "Zone ID is required")

        if self.verifier.enable_multi_zone and zone_id:
            access_token = await self.verifier.verify_token_for_zone(token, zone_id)
        else:
            access_token = await self.verifier.verify_token(token)

        if access_token is None:
            raise KeycardAuthError("invalid_token", "Token verification failed")

        resource_server_url = _get_oauth_protected_resource_url(conn)
        user = KeycardUser(
            access_token=token,
            client_id=access_token.client_id,
            zone_id=zone_id,
            resource_server_url=resource_server_url,
            scopes=access_token.scopes,
        )
        credentials = KeycardAuthCredentials(scopes=access_token.scopes)
        return credentials, user


def _build_unauthorized_response(
    conn: HTTPConnection | Request,
    *,
    error: str = "invalid_token",
    description: str = "Authentication required",
    status_code: int = 401,
) -> Response:
    """Build an RFC 6750 ``WWW-Authenticate`` challenge response.

    Used by ``keycard_on_error`` (when the authentication backend raises) and
    by the ``@requires`` / ``@auth.grant`` decorators (when the request is
    anonymous). The ``resource_metadata=`` URL is computed from the request
    per RFC 9728.
    """
    resource_metadata = _get_oauth_protected_resource_url(conn)
    response = Response(
        content="Unauthorized" if status_code == 401 else "Forbidden",
        status_code=status_code,
    )
    response.headers["WWW-Authenticate"] = _build_challenge_header(
        error, description, resource_metadata
    )
    return response


def keycard_on_error(conn: HTTPConnection, exc: Exception) -> Response:
    """Convert a ``KeycardAuthError`` into an RFC 6750 ``WWW-Authenticate`` challenge.

    Suitable for use as the ``on_error`` argument to
    ``starlette.middleware.authentication.AuthenticationMiddleware``.
    """
    if isinstance(exc, KeycardAuthError):
        return _build_unauthorized_response(
            conn,
            error=exc.error,
            description=exc.description,
            status_code=exc.status_code,
        )
    return _build_unauthorized_response(
        conn,
        description=str(exc) or "Authentication failed",
    )


# ---------------------------------------------------------------------------
# Deprecated legacy surface
# ---------------------------------------------------------------------------
# Preserved so that ``keycardai-mcp`` and ``keycardai-agents`` continue to
# import and use ``BearerAuthMiddleware`` / ``verify_bearer_token`` while a
# follow-up migrates them to ``KeycardAuthBackend`` + ``AuthenticationMiddleware``.
# Do not use these in new ``keycardai-starlette`` code.


async def verify_bearer_token(
    request: Request,
    verifier: TokenVerifier,
    *,
    _from_middleware: bool = False,
) -> dict[str, str | None] | Response:
    """Verify the request's bearer token.

    Returns an auth_info dict on success (suitable for assigning to
    ``request.state.keycardai_auth_info``) or an RFC 6750 challenge
    ``Response`` on failure.

    .. deprecated::
        Kept for ``BearerAuthMiddleware`` compatibility. New code should rely
        on ``KeycardAuthBackend``.
    """
    if not _from_middleware:
        warnings.warn(
            "verify_bearer_token is deprecated and will be removed in a "
            "future release. Use KeycardAuthBackend(verifier) wired to "
            "starlette.middleware.authentication.AuthenticationMiddleware; "
            "results are exposed via request.user / request.auth.",
            DeprecationWarning,
            stacklevel=2,
        )
    if not request.headers.get("Authorization"):
        return _create_auth_challenge_response(
            "invalid_token", "No bearer token provided", request
        )
    token = _get_bearer_token(request)
    if token is None:
        return _create_auth_challenge_response(
            "invalid_token",
            "Invalid Authorization header format",
            request,
            400,
        )

    zone_id = None
    if verifier.enable_multi_zone:
        zone_id = request.path_params.get("zone_id")
        if zone_id is None:
            return _create_auth_challenge_response(
                "invalid_token", "Zone ID is required", request
            )

    if verifier.enable_multi_zone and zone_id:
        access_token = await verifier.verify_token_for_zone(token, zone_id)
    else:
        access_token = await verifier.verify_token(token)
    if access_token is None:
        return _create_auth_challenge_response(
            "invalid_token", "Token verification failed", request
        )

    resource_server_url = _get_oauth_protected_resource_url(request)
    return {
        "access_token": access_token.token,
        "zone_id": zone_id,
        "resource_client_id": resource_server_url,
        "resource_server_url": resource_server_url,
    }


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates OAuth 2.0 bearer tokens.

    On success, populates ``request.state.keycardai_auth_info`` with::

        {
            "access_token": "<verified token>",
            "zone_id": "<zone_id or None>",
            "resource_client_id": "<resource metadata URL>",
            "resource_server_url": "<resource metadata URL>",
        }

    On failure, returns a ``WWW-Authenticate`` challenge per RFC 6750.

    .. deprecated::
        Use ``starlette.middleware.authentication.AuthenticationMiddleware``
        wired to :class:`KeycardAuthBackend` with ``on_error=keycard_on_error``.
        This class will be removed once ``keycardai-mcp`` and
        ``keycardai-agents`` migrate.
    """

    def __init__(self, app: ASGIApp, verifier: TokenVerifier):
        warnings.warn(
            "BearerAuthMiddleware is deprecated and will be removed in a "
            "future release. Use "
            "starlette.middleware.authentication.AuthenticationMiddleware "
            "with backend=KeycardAuthBackend(verifier) and "
            "on_error=keycard_on_error.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(app)
        self.verifier = verifier

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        if _is_oauth_metadata_path(request.url.path):
            return await call_next(request)

        result = await verify_bearer_token(
            request, self.verifier, _from_middleware=True
        )
        if isinstance(result, Response):
            return result
        request.state.keycardai_auth_info = result
        return await call_next(request)
