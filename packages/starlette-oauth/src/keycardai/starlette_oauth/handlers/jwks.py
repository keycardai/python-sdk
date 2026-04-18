"""JWKS endpoint handler for serving public keys."""

from collections.abc import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

from keycardai.oauth.types import JsonWebKeySet


def jwks_endpoint(jwks: JsonWebKeySet) -> Callable:
    """Create a Starlette handler that serves a JSON Web Key Set.

    Args:
        jwks: JSON Web Key Set to serve at this endpoint

    Returns:
        Callable endpoint that serves the JWKS data
    """

    def wrapper(request: Request) -> JSONResponse:
        return JSONResponse(
            content=jwks.model_dump(exclude_none=True),
            status_code=200,
            headers={"Content-Type": "application/json"},
        )

    return wrapper
