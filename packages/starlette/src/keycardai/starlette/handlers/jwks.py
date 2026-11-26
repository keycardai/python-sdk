"""JWKS endpoint handler for serving public keys."""

from collections.abc import Callable

from keycardai.oauth.types import JsonWebKeySet
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .metadata import CORS_HEADERS, _preflight_response

_JWKS_ENDPOINT_ATTR = "_keycard_jwks_endpoint"


def _is_jwks_endpoint(endpoint: object) -> bool:
    return getattr(endpoint, _JWKS_ENDPOINT_ATTR, False) is True


def jwks_endpoint(jwks: JsonWebKeySet) -> Callable:
    """Create a Starlette handler that serves a JSON Web Key Set.

    Args:
        jwks: JSON Web Key Set to serve at this endpoint

    Returns:
        Callable endpoint that serves the JWKS data
    """

    def wrapper(request: Request) -> Response:
        if request.method == "OPTIONS":
            return _preflight_response()

        return JSONResponse(
            content=jwks.model_dump(exclude_none=True),
            status_code=200,
            headers={"Content-Type": "application/json", **CORS_HEADERS},
        )

    wrapper.__dict__[_JWKS_ENDPOINT_ATTR] = True
    return wrapper
