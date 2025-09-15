import time
from typing import Any
import jwt

from pydantic import AnyHttpUrl
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Callable

from keycardai.oauth.utils.jwt import parse_jwt, JasonWebToken, get_jwt_public_key_from_jwks


def _get_oauth_protected_resource_url(request: Request) -> str:
    path = request.url.path.lstrip("/").rstrip("/")
    base_url = str(request.base_url).rstrip("/")
    return str(AnyHttpUrl(f"{base_url}/.well-known/oauth-protected-resource/{path}"))

def _get_bearer_token(request: Request) -> str | None:
    header =request.headers.get("Authorization")
    if header is None or len(header) == 0:
        return None
    parts = header.split(" ")
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1]


def _verify_bearer_token(token: str) -> dict[str, Any] | None:
    try:
        jwt = parse_jwt(token)
    except ValueError:
        return None

    if jwt.payload.get("iss") is None or jwt.header.get("kid") is None:
        return None

    key = get_jwt_public_key_from_jwks(jwt.payload["iss"],jwt.header.get("kid"))
    return None # TODO: complete this!

    
class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, required_scopes: list[str] | None = None, verifier: Callable[[str], dict[str, Any] | None] | None = None):
        super().__init__(app)
        self.required_scopes = required_scopes or []
        self.verifier = verifier or _verify_bearer_token

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        resource_metadata_url = _get_oauth_protected_resource_url(request)
        response = Response()
        if not request.headers.get("Authorization"):
            challenge = f"Bearer error=\"invalid_token\", error_description=\"No bearer token provided\", resource_metadata=\"{resource_metadata_url}\""
            response.headers["WWW-Authenticate"] = challenge
            response.status_code = 401
            response.content = "Unauthorized"
            return response
        token = _get_bearer_token(request)
        if token is None:
            response.status_code = 400
            response.content = "Unauthorized"
            return response

        auth_info = self.verifier(token)
        if auth_info is None:
            challenge = f"Bearer error=\"invalid_token\", error_description=\"Token not intended for resource\", resource_metadata=\"{resource_metadata_url}\""
            response.headers["WWW-Authenticate"] = challenge
            response.status_code = 401
            response.content = "Unauthorized"
            return response

        if self.required_scopes:
            token_scopes = auth_info.get("scope", "").split(" ") if auth_info.get("scope") else []
            if not all(scope in token_scopes for scope in self.required_scopes):
                challenge = f"Bearer error=\"insufficient_scope\", error_description=\"Token does not have the required scopes\", resource_metadata=\"{resource_metadata_url}\""
                response.headers["WWW-Authenticate"] = challenge
                response.status_code = 403
                response.content = "Forbidden"
                return response

        if auth_info["expiresAt"] is None or auth_info["expiresAt"] < time.time() / 1000:
            challenge = f"Bearer error=\"invalid_token\", error_description=\"Token has expired\", resource_metadata=\"{resource_metadata_url}\""
            response.headers["WWW-Authenticate"] = challenge
            response.status_code = 401
            response.content = "Unauthorized"
            return response

        request.state.auth_info = auth_info
        return await call_next(request)
