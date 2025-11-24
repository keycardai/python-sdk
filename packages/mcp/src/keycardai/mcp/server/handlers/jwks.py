"""JWKS endpoint handler for OAuth authentication.

This module provides the JWKS (JSON Web Key Set) endpoint implementation
that serves the public keys used for JWT token verification.
"""

from collections.abc import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

from keycardai.oauth.types import JsonWebKeySet


def jwks_endpoint(jwks: JsonWebKeySet) -> Callable:
    """Create a JWKS endpoint that serves the provided JSON Web Key Set.

    Args:
        jwks: JSON Web Key Set to serve at this endpoint

    Returns:
        Callable endpoint that serves the JWKS data
    """
    def wrapper(request: Request) -> JSONResponse:
        return JSONResponse(
            content=jwks.model_dump(exclude_none=True),
            status_code=200,
            headers={"Content-Type": "application/json"}
        )

    return wrapper
