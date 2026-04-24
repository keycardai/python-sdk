"""Bearer token authentication middleware for Starlette/FastAPI.

Validates incoming bearer tokens using a TokenVerifier and sets
authentication info on the request state for downstream handlers.
"""

from collections.abc import Callable

from pydantic import AnyHttpUrl

from keycardai.oauth.server.verifier import TokenVerifier
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from ..shared.starlette import get_base_url


def _get_oauth_protected_resource_url(request: Request) -> str:
    path = request.url.path.lstrip("/").rstrip("/")
    base_url = get_base_url(request)
    return str(AnyHttpUrl(f"{base_url}/.well-known/oauth-protected-resource/{path}"))


def _get_bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header is None or len(header) == 0:
        return None
    parts = header.split(" ")
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1]


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
    """

    def __init__(self, app: ASGIApp, verifier: TokenVerifier):
        super().__init__(app)
        self.verifier = verifier

    def _create_auth_challenge_response(
        self,
        error: str,
        description: str,
        request: Request,
        status_code: int = 401,
    ) -> Response:
        """Create a standardized OAuth 2.0 Bearer challenge response."""
        resource_metadata_url = _get_oauth_protected_resource_url(request)
        challenge = (
            f'Bearer error="{error}", '
            f'error_description="{description}", '
            f'resource_metadata="{resource_metadata_url}"'
        )

        response = Response(
            content="Unauthorized" if status_code == 401 else "Forbidden"
        )
        response.status_code = status_code
        response.headers["WWW-Authenticate"] = challenge
        return response

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        # OAuth metadata discovery endpoints must remain publicly reachable —
        # they are how clients learn to authenticate in the first place (RFC 9728 §2).
        if request.url.path.startswith("/.well-known/"):
            return await call_next(request)

        if not request.headers.get("Authorization"):
            return self._create_auth_challenge_response(
                "invalid_token", "No bearer token provided", request
            )
        token = _get_bearer_token(request)
        if token is None:
            return self._create_auth_challenge_response(
                "invalid_token",
                "Invalid Authorization header format",
                request,
                400,
            )

        zone_id = None
        if self.verifier.enable_multi_zone:
            zone_id = request.path_params.get("zone_id")
            if zone_id is None:
                return self._create_auth_challenge_response(
                    "invalid_token", "Zone ID is required", request
                )

        if self.verifier.enable_multi_zone and zone_id:
            access_token = await self.verifier.verify_token_for_zone(
                token, zone_id
            )
        else:
            access_token = await self.verifier.verify_token(token)
        if access_token is None:
            return self._create_auth_challenge_response(
                "invalid_token", "Token verification failed", request
            )

        resource_server_url = _get_oauth_protected_resource_url(request)
        request.state.keycardai_auth_info = {
            "access_token": access_token.token,
            "zone_id": zone_id,
            "resource_client_id": resource_server_url,
            "resource_server_url": resource_server_url,
        }
        return await call_next(request)
